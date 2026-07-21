"""
demo_cli.py
------------
Zero-dependency interactive demo of the full Citizen Fraud Shield
conversation flow (language select -> fraud check -> verdict -> guided
NCRB report). Run this to see exactly what a WhatsApp/mobile-app user
would experience, without standing up Flask/FastAPI/Twilio.

Run:
    python demo_cli.py
"""
from core.conversation_engine import FraudShieldConversation, Session


def main():
    print("=" * 60)
    print(" CITIZEN FRAUD SHIELD — CLI DEMO (simulates any channel)")
    print("=" * 60)

    session = Session(session_id="cli-demo", channel="mobile_app")
    convo = FraudShieldConversation(session)

    print("\nBOT:", convo.start())
    while True:
        try:
            user_in = input("\nYOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break
        if user_in.lower() in ("bye", "exit", "quit"):
            print("BOT: Stay safe. Goodbye.")
            break
        reply = convo.handle(user_in)
        print("\nBOT:", reply)


if __name__ == "__main__":
    main()
