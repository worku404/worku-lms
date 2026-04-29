from .base import *

def _csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = config("SECRET_KEY", default=SECRET_KEY)
DEBUG = False

ADMIN = [
    ('Worku W.', 'worku0963@gmail.com'),
]

ALLOWED_HOSTS = _csv(
    config(
        "ALLOWED_HOSTS",
        default="educaproject.com,www.educaproject.com,localhost,127.0.0.1",
    )
)

CSRF_TRUSTED_ORIGINS = _csv(
    config(
        "CSRF_TRUSTED_ORIGINS",
        default=(
            "https://educaproject.com,https://www.educaproject.com,"
            "https://localhost,https://127.0.0.1,"
            "http://localhost,http://127.0.0.1"
        ),
    )
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB'),
        'USER': config('POSTGRES_USER'),
        'PASSWORD': config('POSTGRES_PASSWORD'),
        'HOST': config('POSTGRES_HOST', default='db'),
        'PORT': config('POSTGRES_PORT', default=5432, cast=int),
    }
}
REDIS_URL = config('REDIS_URL', default='redis://cache:6379/1')
CACHES['default']['LOCATION'] = REDIS_URL
CHANNEL_LAYERS['default']['CONFIG']['hosts'] = [REDIS_URL]

#security
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True