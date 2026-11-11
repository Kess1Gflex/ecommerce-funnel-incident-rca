import uuid
import random
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# Set seed for reproducible portfolio numbers
np.random.seed(42)
random.seed(42)

# --- Configuration ---
NUM_SESSIONS = 50000
START_DATE = datetime(2026, 11, 3, 0, 0, 0) # 7-day period (Tue to Mon)
INCIDENT_START = datetime(2026, 11, 6, 18, 30, 0) # Friday 18:30 UTC deploy

devices = ['mobile', 'desktop', 'tablet']
device_weights = [0.65, 0.28, 0.07] # 65% mobile, 28% desktop, 7% tablet

sources = ['google_paid', 'meta_paid', 'organic_search', 'direct']
source_weights = [0.40, 0.35, 0.15, 0.10]

events_data = []
errors_data = []

print("Simulating 50,000 e-commerce sessions...")

for _ in range(NUM_SESSIONS):
    session_id = f"sess_{uuid.uuid4().hex[:10]}"
    user_id = f"usr_{uuid.uuid4().hex[:8]}" if random.random() > 0.3 else None
    device = np.random.choice(devices, p=device_weights)
    source = np.random.choice(sources, p=source_weights)
    
    # Generate random timestamp over the 7-day window
    session_offset = random.uniform(0, 7 * 24 * 3600)
    current_time = START_DATE + timedelta(seconds=session_offset)
    
    # 1. Step: session_start
    events_data.append({
        'event_id': f"evt_{uuid.uuid4().hex[:12]}",
        'session_id': session_id,
        'user_id': user_id,
        'event_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'event_name': 'session_start',
        'device_category': device,
        'traffic_source': source,
        'order_value': None
    })
    
    # 2. Step: view_item (82% transition rate)
    if random.random() > 0.82:
        continue
    current_time += timedelta(seconds=random.randint(15, 60))
    events_data.append({
        'event_id': f"evt_{uuid.uuid4().hex[:12]}",
        'session_id': session_id,
        'user_id': user_id,
        'event_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'event_name': 'view_item',
        'device_category': device,
        'traffic_source': source,
        'order_value': None
    })
    
    # 3. Step: add_to_cart (40% transition rate)
    if random.random() > 0.40:
        continue
    current_time += timedelta(seconds=random.randint(20, 90))
    events_data.append({
        'event_id': f"evt_{uuid.uuid4().hex[:12]}",
        'session_id': session_id,
        'user_id': user_id,
        'event_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'event_name': 'add_to_cart',
        'device_category': device,
        'traffic_source': source,
        'order_value': None
    })
    
    # 4. Step: begin_checkout (75% transition rate)
    if random.random() > 0.75:
        continue
    current_time += timedelta(seconds=random.randint(30, 120))
    events_data.append({
        'event_id': f"evt_{uuid.uuid4().hex[:12]}",
        'session_id': session_id,
        'user_id': user_id,
        'event_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
        'event_name': 'begin_checkout',
        'device_category': device,
        'traffic_source': source,
        'order_value': None
    })
    
    # 5. Step: purchase (The Branching Logic for the Incident)
    is_post_deploy = current_time >= INCIDENT_START
    is_mobile = device == 'mobile'
    
    # Normal baseline conversion: 30%
    checkout_success_prob = 0.30
    
    # The Outage condition: post-Friday deploy on mobile drops conversion to ~1.4%
    if is_post_deploy and is_mobile:
        checkout_success_prob = 0.014
    
    current_time += timedelta(seconds=random.randint(45, 180))
    
    if random.random() <= checkout_success_prob:
        # Success purchase
        order_amount = round(float(np.random.gamma(shape=9, scale=8)), 2) # ~$72 AOV
        events_data.append({
            'event_id': f"evt_{uuid.uuid4().hex[:12]}",
            'session_id': session_id,
            'user_id': user_id,
            'event_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'event_name': 'purchase',
            'device_category': device,
            'traffic_source': source,
            'order_value': order_amount
        })
    else:
        # Checkout failed -> log error row
        if is_post_deploy and is_mobile:
            # 85% of mobile failures post-deploy are the broken token
            err_type = np.random.choice(
                ['apple_pay_token_failure', 'card_declined', 'insufficient_funds', 'gateway_timeout', 'ui_script_error'],
                p=[0.85, 0.08, 0.04, 0.02, 0.01]
            )
        else:
            # Normal baseline error distributions
            err_type = np.random.choice(
                ['card_declined', 'insufficient_funds', 'gateway_timeout', 'ui_script_error'],
                p=[0.60, 0.30, 0.07, 0.03]
            )
            
        os_ver = 'iOS 18.1' if device == 'mobile' and err_type == 'apple_pay_token_failure' else ('iOS 18.0' if device == 'mobile' else 'Windows 11')
        
        errors_data.append({
            'error_id': f"err_{uuid.uuid4().hex[:10]}",
            'session_id': session_id,
            'device_category': device,
            'error_timestamp': current_time.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'error_type': err_type,
            'os_version': os_ver
        })

# Export to CSV
df_events = pd.DataFrame(events_data)
df_errors = pd.DataFrame(errors_data)

df_events.to_csv('web_sessions_stream.csv', index=False)
df_errors.to_csv('checkout_errors_stream.csv', index=False)

print(f"Generated {len(df_events)} event rows -> web_sessions_stream.csv")
print(f"Generated {len(df_errors)} error rows -> checkout_errors_stream.csv")