import base64
import logging
import os
from datetime import timedelta
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]

GOOGLE_CREDENTIALS_FILE = os.getenv(
    "GOOGLE_CREDENTIALS_FILE",
    "credentials.json"
)

GOOGLE_TOKENS_FILE = os.getenv(
    "GOOGLE_TOKENS_FILE",
    "token.json"
)

def get_google_credentials():
    logger.debug("Attempting to get Google credentials")
    creds = None

    if os.path.exists(GOOGLE_TOKENS_FILE):
        logger.info("Loading credentials from %s", GOOGLE_TOKENS_FILE)
        creds = Credentials.from_authorized_user_file(
            GOOGLE_TOKENS_FILE,
            SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired credentials")
            creds.refresh(Request())
        else:
            logger.info(
                "Creating new credentials from %s",
                GOOGLE_CREDENTIALS_FILE
            )
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE,
                SCOPES
            )
            creds = flow.run_local_server(port=0)
            logger.info("New credentials created via OAuth flow")

        with open("token.json", "w") as token:
            token.write(creds.to_json())
        logger.debug("Credentials saved to token.json")

    logger.info("Google credentials obtained successfully")
    return creds


class EmailSender:

    def __init__(self, creds, logger) -> None:
        logger.debug("Initializing EmailSender with Gmail API")
        self.service = build(
            "gmail",
            "v1",
            credentials=creds
        )
        self.logger = logger
        logger.info("EmailSender initialized")

    def build_missing_email(self, df_missing):

        if df_missing.empty:
            self.logger.debug("No missing volumes to include in email")
            return None
        
        total_volumes = len(df_missing)
        total_titles = df_missing["TITLE"].nunique()

        self.logger.info("Building missing volumes email with %d volumes", total_volumes)

        html = f"""
        <!DOCTYPE html>
        <html>
        <body style="
            margin: 0;
            padding: 0;
            background-color: #f5f7fa;
            font-family: Arial, Helvetica, sans-serif;
            color: #1f2937;
        ">

            <div style="
                max-width: 600px;
                margin: 0 auto;
                padding: 24px 12px;
            ">

                <!-- Header -->
                <div style="
                    background-color: #111827;
                    border-radius: 14px 14px 0 0;
                    padding: 28px 24px;
                    color: white;
                ">
                    <div style="
                        font-size: 13px;
                        color: #9ca3af;
                        margin-bottom: 8px;
                        letter-spacing: 0.5px;
                    ">
                        LIGHT NOVEL AND MANGA TRACKER
                    </div>

                    <div style="
                        font-size: 24px;
                        font-weight: bold;
                        margin-bottom: 8px;
                    ">
                        Volúmenes pendientes
                    </div>

                    <div style="
                        font-size: 14px;
                        color: #d1d5db;
                        line-height: 1.5;
                    ">
                        Algunos volúmenes ya fueron publicados
                        y todavía no están en tu colección.
                    </div>
                </div>

                <!-- Summary -->
                <div style="
                    background-color: white;
                    padding: 20px 24px;
                    border-left: 1px solid #e5e7eb;
                    border-right: 1px solid #e5e7eb;
                ">

                    <table width="100%" cellpadding="0" cellspacing="0">
                        <tr>

                            <td width="50%" style="
                                padding-right: 8px;
                            ">
                                <div style="
                                    background-color: #f9fafb;
                                    border: 1px solid #e5e7eb;
                                    border-radius: 10px;
                                    padding: 14px;
                                ">
                                    <div style="
                                        font-size: 24px;
                                        font-weight: bold;
                                        color: #111827;
                                    ">
                                        {total_volumes}
                                    </div>

                                    <div style="
                                        font-size: 12px;
                                        color: #6b7280;
                                        margin-top: 3px;
                                    ">
                                        Volúmenes
                                    </div>
                                </div>
                            </td>

                            <td width="50%" style="
                                padding-left: 8px;
                            ">
                                <div style="
                                    background-color: #f9fafb;
                                    border: 1px solid #e5e7eb;
                                    border-radius: 10px;
                                    padding: 14px;
                                ">
                                    <div style="
                                        font-size: 24px;
                                        font-weight: bold;
                                        color: #111827;
                                    ">
                                        {total_titles}
                                    </div>

                                    <div style="
                                        font-size: 12px;
                                        color: #6b7280;
                                        margin-top: 3px;
                                    ">
                                        Novelas
                                    </div>
                                </div>
                            </td>

                        </tr>
                    </table>

                </div>

                <!-- Books -->
                <div style="
                    background-color: white;
                    padding: 0 24px 24px 24px;
                    border: 1px solid #e5e7eb;
                    border-top: none;
                    border-radius: 0 0 14px 14px;
                ">
        """
        
        for title, group in df_missing.groupby(
            "TITLE",
            sort=False
        ):
            html += f"""
                    <div style="
                        margin-top: 24px;
                    ">

                        <div style="
                            font-size: 17px;
                            font-weight: bold;
                            color: #111827;
                            margin-bottom: 10px;
                        ">
                            {title}
                        </div>
            """

            for _, row in group.iterrows():

                volume = row["VOLUME NO."]
                human_date = row["HUMAN DATE"]

                html += f"""
                        <div style="
                            background-color: #f9fafb;
                            border: 1px solid #e5e7eb;
                            border-radius: 9px;
                            padding: 12px 14px;
                            margin-bottom: 8px;
                        ">

                            <table
                                width="100%"
                                cellpadding="0"
                                cellspacing="0"
                            >
                                <tr>

                                    <td style="
                                        font-size: 14px;
                                        font-weight: bold;
                                        color: #111827;
                                    ">
                                        Vol. {volume}
                                    </td>

                                    <td align="right" style="
                                        font-size: 13px;
                                        color: #6b7280;
                                    ">
                                        {human_date}
                                    </td>

                                </tr>
                            </table>

                        </div>
                """

            html += """
                    </div>
            """

        html += """
                </div>

                <!-- Footer -->
                <div style="
                    text-align: center;
                    padding: 18px 10px 5px 10px;
                    font-size: 11px;
                    color: #9ca3af;
                    line-height: 1.5;
                ">
                    Este mensaje fue generado automáticamente
                    por Light Novel and Manga Tracker.
                </div>

            </div>

        </body>
        </html>
        """

        self.logger.debug("Missing email built successfully")
        return html

    def send_email(self, to_email, subject, html_body):
        self.logger.debug("Preparing email to %s with subject: %s", to_email, subject)

        message = EmailMessage()

        message["To"] = to_email
        message["Subject"] = subject

        message.set_content(
            "Tu cliente de correo no soporta HTML."
        )

        message.add_alternative(
            html_body,
            subtype="html"
        )

        encoded_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        self.service.users().messages().send(
            userId="me",
            body={"raw": encoded_message}
        ).execute()

        self.logger.info(
            "Email sent to %s with subject: %s",
            to_email,
            subject
        )


class CalendarEvents:

    def __init__(self, creds, logger) -> None:
        logger.debug("Initializing CalendarEvents with Google Calendar API")
        self.service = build(
            "calendar",
            "v3",
            credentials=creds
        )
        self.logger = logger
        logger.info("CalendarEvents initialized")

    def sync_calendar(self, df):
        self.logger.info("Starting calendar sync for %d events", len(df))

        for idx, (_, row) in enumerate(df.iterrows(), 1):
            self.logger.debug("Processing calendar event %d/%d", idx, len(df))
            self.sync_calendar_event(row)
        
        self.logger.info("Calendar sync completed for %d events", len(df))

    def sync_calendar_event(self, row):
        title = row["TITLE"]
        volume = row["VOLUME NO."]
        human_date = row["HUMAN DATE"]
        
        self.logger.debug("Syncing calendar event for: %s Vol. %s", title, volume)

        event_id = row["EVENT_ID"]

        release_date = row["ENGLISH RELEASE DATE"]

        start_date = release_date.strftime("%Y-%m-%d")

        end_date = (
            release_date + timedelta(days=1)
        ).strftime("%Y-%m-%d")

        event = {
            "id": event_id,

            "summary": (
                f"📚 {title} — Vol. {volume}"
            ),

            "description": (
                f"📖 {title}\n"
                f"📚 Volumen {volume}\n\n"
                f"📅 Fecha de publicación: {human_date}\n\n"
                f"Este volumen está marcado como próximo "
                f"a publicarse y aún no está en tu colección."
            ),

            "start": {
                "dateTime": f"{start_date}T11:00:00",
                "timeZone": "America/Mexico_City"
            },
            "end": {
                "dateTime": f"{start_date}T11:30:00",
                "timeZone": "America/Mexico_City"
            },

            # Color del evento en Google Calendar
            "colorId": "11",

            # Recordatorio personalizado
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {
                        "method": "popup",
                        "minutes": 24 * 60
                    }
                ]
            },

            "extendedProperties": {
                "private": {
                    "source": "wikipedia_checker"
                }
            }
        }

        try:
            self.service.events().get(
                calendarId="primary",
                eventId=event_id
            ).execute()

            self.service.events().update(
                calendarId="primary",
                eventId=event_id,
                body=event
            ).execute()

            self.logger.info(
                "Calendar event updated: %s — Vol. %s. Date: %s",
                title,
                volume,
                human_date
            )

        except HttpError as error:

            if error.resp.status == 404:
                self.logger.debug("Event not found, creating new event: %s", event_id)

                self.service.events().insert(
                    calendarId="primary",
                    body=event
                ).execute()

                self.logger.info(
                    "Calendar event created: %s — Vol. %s. Date: %s",
                    title,
                    volume,
                    human_date
                )

            else:
                self.logger.error("Error syncing calendar event %s: %s", event_id, error)
                raise