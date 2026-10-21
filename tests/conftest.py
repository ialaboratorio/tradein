import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_tradein.db")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
