import hashlib
import json
import logging
import os
import re
from io import StringIO
from typing import Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from utils.GoogleEvents import (CalendarEvents, EmailSender,
                                get_google_credentials)

load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True
)
logger = logging.getLogger(__name__)


def generate_event_id(title, volume):
    value = f"{title}_vol_{volume}"

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:32]

class BooksNotifications:
    def __init__(self, logger, registry_filename="notifications_registry.json") -> None:
        self.logger = logger
        self.registry_filename = registry_filename
        self.logger.info("Initializing BooksNotifications with registry file: %s", registry_filename)
        
        self.registry = self.open_registry()

    def open_registry(self):
        if os.path.exists(self.registry_filename):
            self.logger.info("Loading registry from %s", self.registry_filename)
            with open(self.registry_filename, 'r', encoding="utf-8") as f:
                try:
                    registry = json.load(f)
                    self.logger.info("Registry loaded successfully with %d entries", len(registry))
                except json.JSONDecodeError as e:
                    self.logger.error("Failed to decode registry file: %s", e)
                    registry = {}  # empty if file was corrupted or empty
        else:
            self.logger.info("Registry file not found. Creating new registry.")
            registry = {}  # create new registry
        return registry

    def flatten_columns(self, columns):
        flattened = []

        for col in columns:
            seen = []
            for value in col:
                value = str(value).strip().upper()

                if value and value not in seen:
                    seen.append(value)

            flattened.append(" ".join(seen))

        return flattened

    def is_valid_isbn(self, value):
        if pd.isna(value):
            return False

        isbn = re.sub(r"\[\d+\]", "", str(value))
        isbn = re.sub(r"[-\s]", "", isbn)

        # ISBN-10
        if len(isbn) == 10:
            if not re.match(r"^\d{9}[\dXx]$", isbn):
                return False

            total = sum(
                (10 - i) * (10 if char.upper() == "X" else int(char))
                for i, char in enumerate(isbn)
            )

            return total % 11 == 0

        # ISBN-13
        if len(isbn) == 13 and isbn.isdigit():
            total = sum(
                int(char) * (1 if i % 2 == 0 else 3)
                for i, char in enumerate(isbn)
            )

            return total % 10 == 0

        return False

    def request_wikipedia_info(self, title):
        self.logger.debug("Requesting Wikipedia info for: %s", title)
        api_url = "https://en.wikipedia.org/w/api.php"

        params = {
            "action": "parse",
            "page": title,
            "prop": "text",
            "format": "json",
            "redirects": 1
        }


        headers = {
            "User-Agent": (
                "WikipediaChecker/1.0 "
                "(personal light novel tracker)"
            )
        }

        response = requests.get(
            api_url,
            params=params,
            headers=headers,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        html = data["parse"]["text"]["*"]
        self.logger.debug("Successfully retrieved Wikipedia info for: %s", title)
        
        return html

    def soup_html(self, html):
        return BeautifulSoup(html, "html.parser")

    def normalize_df(self, df):
        self.logger.debug("Normalizing dataframe with columns: %s", list(df.columns))
        vol_name = next(
            (col for col in df.columns
            if col.upper() in {"VOLUME NO.", "NO."}),
            None
        )
        date_name = next(
                (col for col in df.columns
                if col.upper() in {"RELEASE DATE", "ENGLISH RELEASE DATE", "ENGLISH RELEASE DATE PHYSICAL"}),
                None
            )
        ISBN_name = next(
                    (col for col in df.columns
                    if col.upper() in {"ENGLISH ISBN", "ISBN"}),
                    None
                )
        
        if vol_name is None:
            self.logger.error("Volume number column not found in dataframe")
            raise ValueError("Volume number column not found.")

        if date_name is None:
            self.logger.error("English release date column not found in dataframe")
            raise ValueError("English release date column not found.")

        if ISBN_name is None:
            self.logger.error("ISBN column not found in dataframe")
            raise ValueError("ISBN column not found.")
        
        df = df.rename({vol_name: "VOLUME NO.",
                        date_name: "ENGLISH RELEASE DATE",
                        ISBN_name: "ISBN"}, axis=1)
        
        df["ENGLISH RELEASE DATE"] = (
            df["ENGLISH RELEASE DATE"]
            .str.replace(r"\[\d+\]", "", regex=True)
            .str.strip()
        )

        df["ENGLISH RELEASE DATE"] = pd.to_datetime(
            df["ENGLISH RELEASE DATE"],
            errors="coerce"
        )
        
        meses = {
            1: "Enero",
            2: "Febrero",
            3: "Marzo",
            4: "Abril",
            5: "Mayo",
            6: "Junio",
            7: "Julio",
            8: "Agosto",
            9: "Septiembre",
            10: "Octubre",
            11: "Noviembre",
            12: "Diciembre"
        }

        df["HUMAN DATE"] = df["ENGLISH RELEASE DATE"].apply(
            lambda x: f"{x.day} de {meses[x.month]}, {x.year}"
            if pd.notna(x) else None
        )
        
        self.logger.debug("Dataframe normalized successfully with %d rows", len(df))
        return df
    
    def get_previous_heading(self, heading):
        if not heading:
            return None

        level = int(heading.name[1:])

        if level <= 1:
            return None

        return heading.find_previous(f"h{level - 1}")

    def find_relevant_table(self, soup, heading_title="light novel"):
        self.logger.debug("Searching for table with heading type: %s", heading_title)
        for table_tag in soup.find_all("table", class_="wikitable"):
            
            heading = table_tag.find_previous(["h2", "h3", "h4"])
            heading_prev = self.get_previous_heading(heading)
            
            title = heading.get_text(" ", strip=True) if heading else ""
            title_prev = heading_prev.get_text(" ", strip=True) if heading_prev else ""
            
            for row in table_tag.find_all("tr"):
                if row.find("td", attrs={"colspan": True}):
                    row.decompose()
                    
            rows = table_tag.find_all("tr")

            if not rows:
                continue
                    
            headers = [
                cell.get_text(" ", strip=True).upper()
                for cell in table_tag.find_all("tr")[0].find_all(["th", "td"])
            ]
            
            if "ENGLISH ISBN" in headers:
                isbn_column = "ENGLISH ISBN"
            elif "ISBN" in headers:
                isbn_column = "ISBN"
            else:
                isbn_column = None
                    
            recover_df = (
                heading
                and heading_title == title.lower()
            ) or (
                heading_prev
                and heading_title == title_prev.lower()
            )

            if not recover_df:
                continue
            
            df = pd.read_html(StringIO(str(table_tag)))[0]

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = self.flatten_columns(df.columns)
            else:
                df.columns = df.columns.str.upper()

            if isbn_column:
                valid_isbn = df[isbn_column].apply(self.is_valid_isbn)

                if valid_isbn.any():
                    self.logger.info("Found relevant table for heading: %s", heading_title)
                    return self.normalize_df(df)
            else:
                self.logger.info("Found relevant table for heading: %s (no ISBN column)", heading_title)
                return self.normalize_df(df)

        self.logger.debug("No relevant table found for heading type: %s", heading_title)
        return None
    
    def create_notifications(self, df, volumes_purchased) -> Tuple[pd.DataFrame, pd.DataFrame]:
        today = pd.Timestamp.today().normalize()
        self.logger.debug("Creating notifications with %d volumes purchased", len(volumes_purchased))
        df["PURCHASED"] = df["VOLUME NO."].apply(lambda x: str(x) in volumes_purchased)
        df = df[
            (~df["PURCHASED"]) &
            (df["HUMAN DATE"].notna())
        ]
        df = df[["TITLE", "VOLUME NO.", "ENGLISH RELEASE DATE", "HUMAN DATE"]]
        df["RELEASED"] = df["ENGLISH RELEASE DATE"] < today
        df["UPCOMING"] = df["ENGLISH RELEASE DATE"] >= today
        df["EVENT_ID"] = [
            generate_event_id(title, volume)
            for title, volume in zip(
                df["TITLE"],
                df["VOLUME NO."]
            )
        ]
        
        
        df_missing = df[(df["RELEASED"])]
        df_upcoming = df[(df["UPCOMING"])]
        self.logger.info("Created notifications: %d missing, %d upcoming", len(df_missing), len(df_upcoming))
                    
        return df_missing, df_upcoming
    
    def get_updates(self) -> Tuple[pd.DataFrame, pd.DataFrame]:
        self.logger.info("Starting to retrieve updates for %d titles", len(self.registry))
        missing_frames = []
        upcoming_frames = []
        
        for title, title_info in self.registry.items():
            self.logger.debug("Processing title: %s (Wikipedia: %s)", title, title_info.get("wikipedia_title"))
            try:
                html = self.request_wikipedia_info(title_info["wikipedia_title"])
            except Exception as e:
                self.logger.exception("Error processing Wikipedia Info for %s. Error %s", title, e)
                continue
            try:
                soup = self.soup_html(html)
            except Exception as e:
                self.logger.exception("Error processing HTML Info for %s. Error %s", title, e)
                continue
            try:
                df = self.find_relevant_table(soup, title_info["heading_type"])
            except Exception as e:
                self.logger.exception("Error processing Table Info for %s. Error %s", title, e)
                continue
            if df is not None:
                df["TITLE"] = title                
                df_missing, df_upcoming = self.create_notifications(df, title_info["volumes_purchased"])
                missing_frames.append(df_missing)
                upcoming_frames.append(df_upcoming)
                
        df_missing_final = (
            pd.concat(missing_frames, ignore_index=True)
            if missing_frames
            else pd.DataFrame()
        )

        df_upcoming_final = (
            pd.concat(upcoming_frames, ignore_index=True)
            if upcoming_frames
            else pd.DataFrame()
        )
        self.logger.info("Finished retrieving updates: %d missing, %d upcoming volumes", len(df_missing_final), len(df_upcoming_final))
        return df_missing_final, df_upcoming_final
    
    def send_missing_volumes_email(self, creds, df, to_email):
        self.logger.info("Sending email for %d missing volumes to %s", len(df), to_email)
        email_class = EmailSender(creds, self.logger)
        html_body = email_class.build_missing_email(df)

        if html_body:
            email_class.send_email(
                to_email,
                f"Light Novels and Manga — {len(df)} pendientes",
                html_body
            )
            self.logger.info("Email sent successfully for missing volumes")
        else:
            self.logger.warning("No email body generated for missing volumes")
        
    def sync_calendar_events(self, creds, df):
        self.logger.info("Syncing calendar with %d upcoming volumes", len(df))
        calendar_class = CalendarEvents(creds, self.logger)
        calendar_class.sync_calendar(df)
        self.logger.info("Calendar sync completed")

if __name__ == "__main__":
    logger.info("Starting Wikipedia Checker application")
    notifier = BooksNotifications(logger)
    
    df_missing, df_upcoming = notifier.get_updates()
    
    if df_missing.empty and df_upcoming.empty:
        logger.info("No updates found.")
    else:
        logger.info("Processing updates: %d missing, %d upcoming", len(df_missing), len(df_upcoming))
        
        notification_email = os.getenv("NOTIFICATION_EMAIL")
        
        if not notification_email:
            logger.error("NOTIFICATION_EMAIL environment variable is not configured")
            raise RuntimeError(
                "NOTIFICATION_EMAIL environment variable is not configured."
            )
            
        creds = get_google_credentials()
        logger.debug("Google credentials obtained")

        if not df_missing.empty:
            logger.info("Sending email notification for missing volumes")
            notifier.send_missing_volumes_email(
                creds,
                df_missing,
                notification_email
            )

        if not df_upcoming.empty:
            logger.info("Syncing upcoming volumes to calendar")
            notifier.sync_calendar_events(
                creds,
                df_upcoming
            )
    
    logger.info("Wikipedia Checker application completed")
