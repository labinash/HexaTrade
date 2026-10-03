from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_redirect_url: str = "http://127.0.0.1:8000/api/broker/callback"
    app_master_key: str = ""

    sqlserver_connection_string: str = ""
    sqlserver_host: str = r"localhost\SQLEXPRESS"
    sqlserver_database: str = "HexTrade"
    sqlserver_trusted_connection: str = "yes"
    sqlserver_user: str = ""
    sqlserver_password: str = ""
    sqlserver_odbc_driver: str = ""

    redis_url: str = "redis://localhost:6379/0"
    cors_origin: str = "http://localhost:5173"
    stale_seconds: int = 5
    risk_free_rate: float = 0.07

    @property
    def trusted_connection(self) -> bool:
        return self.sqlserver_trusted_connection.strip().lower() in {"1", "true", "yes"}

    def odbc_connection_string(self, database: str, driver: str) -> str:
        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={self.sqlserver_host}",
            f"DATABASE={database}",
            "TrustServerCertificate=yes",
            "LoginTimeout=5",
            "Connection Timeout=5",
        ]
        if self.trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.sqlserver_user}")
            parts.append(f"PWD={self.sqlserver_password}")
        return ";".join(parts) + ";"

    def sqlalchemy_url(self, driver: str, database: str | None = None) -> str:
        if self.sqlserver_connection_string.strip() and database in (None, self.sqlserver_database):
            odbc = normalize_odbc(self.sqlserver_connection_string, driver)
            if odbc.lower().startswith("mssql+"):
                return odbc
            return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"
        database = database or self.sqlserver_database
        odbc = self.odbc_connection_string(database, driver)
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc)}"


def normalize_odbc(raw: str, driver: str) -> str:
    text = raw.strip().strip('"').strip("'")
    if text.lower().startswith("mssql+"):
        return text
    if "driver=" not in text.lower():
        text = f"DRIVER={{{driver}}};{text}"
    if "trustservercertificate=" not in text.lower():
        text = text.rstrip(";") + ";TrustServerCertificate=yes"
    return text


def get_settings() -> Settings:
    return Settings()
