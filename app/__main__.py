"""``python -m app`` — serve the OpenFPL planner on http://127.0.0.1:8410."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8410, log_level="info")
