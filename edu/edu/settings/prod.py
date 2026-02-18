from .base import *

DEBUG = False

ADMIN = [
    ('Worku W.', 'worku0963@gmail.com'),
]

ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3'
    }
}