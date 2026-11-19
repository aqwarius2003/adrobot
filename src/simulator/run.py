"""Standalone runner for the Keitaro Admin API simulator."""

import os

import uvicorn

from src.simulator.app import simulator_app

if __name__ == "__main__":
    host = os.getenv("SIMULATOR_HOST", "0.0.0.0")
    port = int(os.getenv("SIMULATOR_PORT", "8001"))
    uvicorn.run(simulator_app, host=host, port=port)
