from flask import Flask, render_template, request, jsonify
import googlemaps
import os
from datetime import datetime, timedelta
from functools import wraps
import logging
from logging.handlers import RotatingFileHandler
import time

app = Flask(__name__)

# Load environment variables
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()
    app.logger.info('Loaded local .env file')

# Initialize Google Maps client with timeout

gmaps = googlemaps.Client(key=os.getenv('GOOGLE_MAPS_API_KEY'))

class DailyRateLimiter:
    def __init__(self, max_calls_per_day):
        self.max_calls = max_calls_per_day
        self.calls_made = 0
        self.current_date = datetime.now().date()
        self.lock = False
        
    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Thread-safe counter management
            while self.lock:
                time.sleep(0.1)
            
            self.lock = True
            today = datetime.now().date()
            
            if today != self.current_date:
                self.calls_made = 0
                self.current_date = today
                app.logger.info(f'Rate limit reset for new day: {today}')
            
            if self.calls_made >= self.max_calls:
                self.lock = False
                app.logger.warning(f'Daily API limit reached ({self.max_calls} calls)')
                raise Exception(f"Daily API limit reached ({self.max_calls} calls)")
                
            self.calls_made += 1
            app.logger.debug(f'API call {self.calls_made}/{self.max_calls}')
            self.lock = False
            return func(*args, **kwargs)
        return wrapper

# Initialize rate limiter with 25 calls/day
daily_rate_limiter = DailyRateLimiter(max_calls_per_day=25)

@app.route('/', methods=['GET', 'POST'])
def index():
    businesses = []
    error = None
    
    if request.method == 'POST':
        location = request.form.get('location', '').strip()
        business_type = request.form.get('business_type', '').strip()
        
        if not location or not business_type:
            error = "Please provide both location and business type"
            app.logger.warning('Missing form data')
        else:
            try:
                @daily_rate_limiter
                def get_geocode(location):
                    return gmaps.geocode(location)
                
                geocode_result = get_geocode(location)
                
                if not geocode_result:
                    error = "Location not found"
                    app.logger.warning(f'Geocode failed for location: {location}')
                else:
                    lat = geocode_result[0]['geometry']['location']['lat']
                    lng = geocode_result[0]['geometry']['location']['lng']
                    
                    @daily_rate_limiter
                    def get_places_nearby(**kwargs):
                        return gmaps.places_nearby(**kwargs)
                    
                    places_result = get_places_nearby(
                        location=(lat, lng),
                        radius=5000,
                        type=business_type.lower(),
                        open_now=False
                    )
                    
                    for place in places_result.get('results', [])[:5]:
                        @daily_rate_limiter
                        def get_place_details(place_id, fields):
                            return gmaps.place(place_id, fields=fields)
                        
                        try:
                            place_details = get_place_details(
                                place['place_id'],
                                fields=['name', 'formatted_phone_number', 'formatted_address', 'website']
                            )
                            
                            businesses.append({
                                'name': place_details['result'].get('name', 'N/A'),
                                'phone': place_details['result'].get('formatted_phone_number', 'N/A'),
                                'address': place_details['result'].get('formatted_address', 'N/A'),
                                'website': place_details['result'].get('website', 'N/A')
                            })
                        except Exception as e:
                            app.logger.error(f'Failed to get place details: {str(e)}')
                            continue
                    
            except Exception as e:
                error = "Service temporarily unavailable" if "limit reached" in str(e) else str(e)
                app.logger.error(f'API Error: {error}')
    
    return render_template('index.html', businesses=businesses, error=error)

@app.route('/api-usage')
def api_usage():
    return jsonify({
        'calls_today': daily_rate_limiter.calls_made,
        'daily_limit': daily_rate_limiter.max_calls,
        'current_date': daily_rate_limiter.current_date.isoformat(),
        'status': 'success'
    })

@app.errorhandler(404)
def not_found_error(error):
    app.logger.error(f'404 Error: {str(error)}')
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.critical(f'500 Error: {str(error)}')
    return render_template('500.html'), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)