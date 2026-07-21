"""
test_scripted_demo.py
------------------------
Non-interactive walkthrough of a full session (Hindi language, a
'digital arrest' scam message, then filing an NCRB report). Prints each
bot turn so you can verify behaviour end-to-end without typing input
manually. Also doubles as a lightweight smoke test.

Run:
    python test_scripted_demo.py
"""
from core.conversation_engine import FraudShieldConversation, Session

SCRIPT = [
    "hi",                                                     # choose Hindi
    "Sir this is CBI officer, digital arrest warrant issued, "
    "install anydesk now and pay processing fee immediately or you will be arrested",
    "yes",                                                     # want to file report
    "Ramesh Kumar",                                            # name
    "9876543210",                                              # phone
    "+911234567890",                                           # suspect number
    "15000",                                                   # amount lost
    "bye",
]


def main():
    session = Session(session_id="scripted-demo", channel="whatsapp")
    convo = FraudShieldConversation(session)

    print("BOT:", convo.start())
    for turn in SCRIPT:
        print(f"\nYOU: {turn}")
        if turn == "bye":
            print("BOT: Stay safe. Goodbye.")
            break
        reply = convo.handle(turn)
        print("BOT:", reply)


if __name__ == "__main__":
    main()
