#!/usr/bin/env python3
"""
Quick script to check motion database state
"""

import sqlite3
import requests

print("🔍 === CHECKING MOTION DATABASE STATE ===")

# Check database directly
print("\n📊 Database Check:")
try:
    conn = sqlite3.connect('src/ai2d_chat.db')
    cursor = conn.cursor()
    
    # Count motions
    cursor.execute('SELECT COUNT(*) FROM live2d_motions')
    motion_count = cursor.fetchone()[0]
    print(f"Total motions in database: {motion_count}")
    
    # Count by model
    cursor.execute('SELECT model_name, COUNT(*) FROM live2d_motions GROUP BY model_name')
    model_counts = cursor.fetchall()
    print("Motions by model:")
    for model_name, count in model_counts:
        print(f"  {model_name}: {count}")
    
    # Count expressions
    cursor.execute('SELECT COUNT(*) FROM live2d_expressions')
    expr_count = cursor.fetchone()[0]
    print(f"Total expressions in database: {expr_count}")
    
    conn.close()
    
except Exception as e:
    print(f"❌ Database error: {e}")

# Check API
print("\n🌐 API Check:")
try:
    # Try to get port from config
    import yaml
    try:
        with open('../../config.yaml', 'r') as f:
            config = yaml.safe_load(f)
        port = config.get('server', {}).get('port', 19080)
    except Exception:
        port = 19080  # Default from config.yaml
        
    response = requests.get(f'http://localhost:{port}/api/live2d/model/kanade/motions')
    print(f"API status: {response.status_code}")
    if response.ok:
        motions = response.json()
        print(f"API returned {len(motions)} motions for kanade")
    else:
        print(f"API error: {response.text}")
except Exception as e:
    print(f"❌ API error: {e}")

print("\n✅ Check complete")
