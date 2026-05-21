import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY       = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
    GOOGLE_MAPS_KEY  = os.environ.get('GOOGLE_MAPS_KEY', '')  # set in .env
    DATABASE         = os.path.join(os.path.dirname(__file__), 'clinicfinder.db')
    SEARCH_RADIUS_KM = 25  # default radius in km — user can change it in the sidebar
    WAIT_EXPIRY_HRS  = 3   # how long a wait report stays "current" before falling back to estimate
