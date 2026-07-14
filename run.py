import os
import threading
import webbrowser

from waitress import serve

from app import create_app


HOST = "127.0.0.1"
PORT = 5050


def open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    app = create_app()

    if os.environ.get("ADBL_NO_BROWSER") != "1":
        threading.Timer(
            1.2,
            open_browser,
        ).start()

    print()
    print("ADBL Label Printer")
    print(f"Adres: http://{HOST}:{PORT}")
    print(
        "Tryb:",
        "TESTOWY"
        if app.config["TEST_MODE"]
        else "PRODUKCYJNY",
    )
    print("Aby zatrzymać program, naciśnij Ctrl+C.")
    print()

    serve(
        app,
        host=HOST,
        port=PORT,
        threads=4,
    )