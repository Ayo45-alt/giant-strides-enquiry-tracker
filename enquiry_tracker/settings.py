"""
Django settings for Giant Strides Enquiry Tracker.

Architecture:
    - Environment-driven configuration
    - SQLite development database
    - Django-Q background task processing
    - Telegram, WhatsApp and email integrations
    - Recruitment and staff management applications
    - Production-aware security configuration

Generated for Django 6.x.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# ============================================================
# 1. BASE PATHS AND ENVIRONMENT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from the project root.
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    """Safely convert environment variables to boolean values."""
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1", "true", "yes", "on"
    }


def env_list(name, default=None):
    """Read comma-separated environment variables."""
    value = os.getenv(name)

    if value is None:
        return default or []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def env_int(name, default):
    """Read integer environment variables with validation."""
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must contain a valid integer."
        )


# ============================================================
# 2. APPLICATION ENVIRONMENT
# ============================================================

APP_ENVIRONMENT = os.getenv(
    "APP_ENVIRONMENT",
    "development"
).strip().lower()

IS_PRODUCTION = APP_ENVIRONMENT == "production"

DEBUG = env_bool("DJANGO_DEBUG", not IS_PRODUCTION)


# ============================================================
# 3. SECURITY CONFIGURATION
# ============================================================

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ValueError(
            "DJANGO_SECRET_KEY must be configured in production."
        )

    SECRET_KEY = (
        "development-only-secret-key-"
        "replace-this-before-deployment"
    )

if IS_PRODUCTION and DEBUG:
    raise ValueError(
        "DEBUG must be False in production."
    )


'''ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    default=[] if IS_PRODUCTION else [
        "127.0.0.1",
        "localhost",
    ],
)'''

ALLOWED_HOSTS = ["*"]

if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise ValueError(
        "DJANGO_ALLOWED_HOSTS must be configured in production."
    )


CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS"
)


# HTTPS / Reverse proxy configuration.
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("DJANGO_SECURE_PROXY_SSL_HEADER", False)
    else None
)

SECURE_SSL_REDIRECT = env_bool(
    "DJANGO_SECURE_SSL_REDIRECT",
    IS_PRODUCTION,
)

SESSION_COOKIE_SECURE = env_bool(
    "DJANGO_SESSION_COOKIE_SECURE",
    IS_PRODUCTION,
)

CSRF_COOKIE_SECURE = env_bool(
    "DJANGO_CSRF_COOKIE_SECURE",
    IS_PRODUCTION,
)

SECURE_CONTENT_TYPE_NOSNIFF = True

X_FRAME_OPTIONS = "DENY"

SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

SECURE_HSTS_SECONDS = env_int(
    "DJANGO_SECURE_HSTS_SECONDS",
    31536000 if IS_PRODUCTION else 0,
)

SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS",
    IS_PRODUCTION,
)

SECURE_HSTS_PRELOAD = env_bool(
    "DJANGO_SECURE_HSTS_PRELOAD",
    False,
)


# ============================================================
# 4. APPLICATION REGISTRATION
# ============================================================

INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Internal applications
    "tracker",
    "recruitment",

    # Background task processing
    "django_q",
]


# ============================================================
# 5. DJANGO-Q CONFIGURATION
# ============================================================

Q_CLUSTER = {
    "name": "GiantStridesCluster",

    "workers": env_int(
        "DJANGO_Q_WORKERS",
        2,
    ),

    "recycle": env_int(
        "DJANGO_Q_RECYCLE",
        500,
    ),

    "timeout": env_int(
        "DJANGO_Q_TIMEOUT",
        60,
    ),

    "retry": env_int(
        "DJANGO_Q_RETRY",
        90,
    ),

    "orm": "default",
}


if Q_CLUSTER["retry"] <= Q_CLUSTER["timeout"]:
    raise ValueError(
        "DJANGO_Q_RETRY must be greater than DJANGO_Q_TIMEOUT."
    )


# ============================================================
# 6. MIDDLEWARE
# ============================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",

    "django.middleware.common.CommonMiddleware",

    "django.middleware.csrf.CsrfViewMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",

    "django.contrib.messages.middleware.MessageMiddleware",

    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ============================================================
# 7. URL AND APPLICATION ENTRY POINTS
# ============================================================

ROOT_URLCONF = "enquiry_tracker.urls"

WSGI_APPLICATION = "enquiry_tracker.wsgi.application"


# ============================================================
# 8. TEMPLATE CONFIGURATION
# ============================================================

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",

        "DIRS": [
            BASE_DIR / "templates",
        ],

        "APP_DIRS": True,

        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",

                "django.template.context_processors.request",

                "django.contrib.auth.context_processors.auth",

                "django.contrib.messages.context_processors.messages",

                # Staff dashboard navigation
                "tracker.context_processors.staff_navigation",

                # Recruitment dashboard statistics
                "recruitment.context_processors.recruitment_counts",
            ],
        },
    },
]


# ============================================================
# 9. DATABASE CONFIGURATION
# ============================================================

'''DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",

        "NAME": BASE_DIR / "db.sqlite3",

        "OPTIONS": {
            "timeout": 20,
        },
    }
}'''
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }
}

# ============================================================
# 10. PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "MinimumLengthValidator"
        ),
        "OPTIONS": {
            "min_length": 10,
        },
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "CommonPasswordValidator"
        ),
    },
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "NumericPasswordValidator"
        ),
    },
]


# ============================================================
# 11. AUTHENTICATION AND REDIRECTS
# ============================================================

LOGIN_URL = "/tracker/login/"

LOGIN_REDIRECT_URL = "/tracker/"

LOGOUT_REDIRECT_URL = "/tracker/login/"


# ============================================================
# 12. INTERNATIONALIZATION
# ============================================================

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Africa/Lagos"

USE_I18N = True

USE_TZ = True


# ============================================================
# 13. STATIC FILE CONFIGURATION
# ============================================================

STATIC_URL = "/static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
] if (BASE_DIR / "static").exists() else []


# ============================================================
# 14. MEDIA / FILE UPLOAD CONFIGURATION
# ============================================================

MEDIA_URL = "/media/"

MEDIA_ROOT = BASE_DIR / "media"


# Upload limits.
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000


# ============================================================
# 15. EMAIL CONFIGURATION
# ============================================================

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)

EMAIL_HOST = os.getenv(
    "EMAIL_HOST",
    "",
)

EMAIL_PORT = env_int(
    "EMAIL_PORT",
    587,
)

EMAIL_USE_TLS = env_bool(
    "EMAIL_USE_TLS",
    True,
)

EMAIL_USE_SSL = env_bool(
    "EMAIL_USE_SSL",
    False,
)

if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ValueError(
        "EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True."
    )

EMAIL_HOST_USER = os.getenv(
    "EMAIL_HOST_USER",
    "",
)

EMAIL_HOST_PASSWORD = os.getenv(
    "EMAIL_HOST_PASSWORD",
    "",
)

DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "Giant Strides <notifications@example.com>",
)

SERVER_EMAIL = DEFAULT_FROM_EMAIL

EMAIL_TIMEOUT = env_int(
    "EMAIL_TIMEOUT",
    20,
)

TUTORS_GROUP_EMAIL = os.getenv(
    "TUTORS_GROUP_EMAIL",
    "",
)


# ============================================================
# 16. TELEGRAM INTEGRATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
)

TELEGRAM_TUTORS_GROUP_ID = os.getenv(
    "TELEGRAM_TUTORS_GROUP_ID",
    "",
)


# ============================================================
# 17. WHATSAPP / GREEN API INTEGRATION
# ============================================================

GREEN_API_ID_INSTANCE = os.getenv(
    "GREEN_API_ID_INSTANCE",
    "",
)

GREEN_API_TOKEN = os.getenv(
    "GREEN_API_TOKEN",
    "",
)

WHATSAPP_TUTORS_GROUP_ID = os.getenv(
    "WHATSAPP_TUTORS_GROUP_ID",
    "",
)

GREEN_API_URL = os.getenv(
    "GREEN_API_URL",
    ""
)


# ============================================================
# 18. GOOGLE CALENDAR / MEET INTEGRATION
# ============================================================

GOOGLE_OAUTH_CLIENT_ID = os.getenv(
    "GOOGLE_OAUTH_CLIENT_ID",
    "",
)

GOOGLE_OAUTH_CLIENT_SECRET = os.getenv(
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "",
)

GOOGLE_OAUTH_REDIRECT_URI = os.getenv(
    "GOOGLE_OAUTH_REDIRECT_URI",
    "http://127.0.0.1:8000/google/oauth/callback/",
)

GOOGLE_OAUTH_SCOPE = os.getenv(
    "GOOGLE_OAUTH_SCOPE",
    "https://www.googleapis.com/auth/calendar.events.owned",
)

GOOGLE_CALENDAR_ACCOUNT_EMAIL = os.getenv(
    "GOOGLE_CALENDAR_ACCOUNT_EMAIL",
    "giantstridesonlineschool1@gmail.com",
)

GOOGLE_CALENDAR_TOKEN_FILE = (
    BASE_DIR / "google_calendar_token.json"
)

# ============================================================
# 18. LOGGING CONFIGURATION
# ============================================================
SITE_DOMAIN = "http://127.0.0.1:8000"
LOGGING = {
    "version": 1,

    "disable_existing_loggers": False,

    "formatters": {
        "verbose": {
            "format": (
                "{levelname} {asctime} "
                "{name} {message}"
            ),
            "style": "{",
        },

        "simple": {
            "format": "{levelname}: {message}",
            "style": "{",
        },
    },

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },

    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.getenv(
                "DJANGO_LOG_LEVEL",
                "INFO",
            ),
            "propagate": True,
        },

        "recruitment": {
            "handlers": ["console"],
            "level": os.getenv(
                "RECRUITMENT_LOG_LEVEL",
                "INFO",
            ),
            "propagate": False,
        },
    },
}


# ============================================================
# 19. DEFAULT MODEL PRIMARY KEY
# ============================================================

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ============================================================
# 20. PRODUCTION VALIDATION
# ============================================================

if IS_PRODUCTION:

    if EMAIL_BACKEND == (
        "django.core.mail.backends.console.EmailBackend"
    ):
        raise ValueError(
            "Production email must use a real email backend."
        )

    if not SECURE_SSL_REDIRECT:
        raise ValueError(
            "HTTPS redirect must be enabled in production."
        )
