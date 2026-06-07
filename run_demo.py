"""PulseLink Demo Runner — starts the backend API server.

Usage:
    python run_demo.py

This starts the unified demo API on port 8000. The three frontend apps
(patient-app, donor-screen, coordinator-dashboard) can then be started
separately via `npm run dev` in their respective directories.

Frontend URLs (after starting their dev servers):
  - Patient App:             http://localhost:5173/?patient=<id>&token=demo&lang=en
  - Donor Accept/Decline:    http://localhost:5174/?lang=te&slot=<id>&token=demo&start=2025-01-10&end=2025-01-14&units=2
  - Coordinator Dashboard:   http://localhost:5175/?city=hyderabad

Backend API:
  - Health:     GET  http://localhost:8000/health
  - Patients:   GET  http://localhost:8000/coordinator/patients?city=hyderabad
  - Subscription: GET http://localhost:8000/patient/subscription?patient=<id>&token=demo
  - Respond:    POST http://localhost:8000/donor/respond  {slot_id, token, response}
  - Parse:      POST http://localhost:8000/parse  {rawText, cityId}
"""

import os
import sys

# Ensure the backend package is importable
backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, backend_dir)

# Set environment to use mock providers
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("MESSAGE_CHANNEL", "mock")
os.environ.setdefault("VOICE_PROVIDER", "mock")
os.environ.setdefault("EVENT_BUS", "memory")
os.environ.setdefault("DATASET_CSV_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "Dataset.csv"))

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  PulseLink Demo — The Blood Subscription for Thalassemia")
    print("=" * 60)
    print()
    print("Starting unified API server on http://localhost:8000")
    print()
    print("Frontend dev servers (run separately):")
    print("  cd frontend/patient-app && npm install && npm run dev")
    print("  cd frontend/donor-screen && npm install && npm run dev")
    print("  cd frontend/coordinator-dashboard && npm install && npm run dev")
    print()
    print("API Docs: http://localhost:8000/docs")
    print()

    uvicorn.run(
        "pulselink.demo.api:app",
        host="0.0.0.0",
        port=8000,
        reload=False,  # Keep False during live call testing — reload wipes in-memory sessions
    )
