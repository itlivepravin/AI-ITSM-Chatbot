# HTML Chatbot

This is a simple static frontend for the ITSM Flask API.

## Run the API

1. In [main.py](../main.py), set `USE_API_FLAG = True`.
2. Start the server:
   ```
   python main.py
   ```

The API should be available at `http://127.0.0.1:5000`.

## Open the UI

Open [index.html](index.html) in a browser.

## Notes

- The User ID must exist in the database.
- The UI sends POST requests to `/api/chat`.
