"""
core/seed_training_data.py
----------------------------
Bootstrap examples for the adaptive ML classifier (core/ml_risk_model.py).

Why this exists: a from-scratch classifier with zero examples can't
predict anything. These ~90 short labeled examples (scam=1 / not-scam=0)
give the model a reasonable starting point on day one, covering every
signal category the rule engine knows about, IN ADDITION TO plain
everyday messages so it doesn't just learn "any message = scam".

This is intentionally small and hand-written for a hackathon/MVP -- the
real learning happens afterwards, from live citizen/admin feedback via
POST /feedback, which is what actually makes this "adaptive" rather than
a static demo. See core/ml_risk_model.py for how new feedback is folded
in with partial_fit.
"""

from __future__ import annotations

SCAM_EXAMPLES = [
    "Sir this is CBI officer speaking there is a digital arrest warrant against you install anydesk immediately and pay processing fee",
    "This is Mumbai police cyber cell your aadhaar is linked to a crime you will be arrested today unless you pay",
    "Your account will be blocked today please update your KYC by sharing OTP sent to your phone now",
    "RBI has flagged your debit card for suspicious activity share your card number and OTP to unblock",
    "Congratulations you have won a lottery of 25 lakh rupees pay processing fee to claim your prize money now",
    "You have won a lucky draw KBC prize of 15 lakhs send your bank details to claim it",
    "Dear customer your parcel is held at customs pay a small customs duty to release your courier package",
    "Please install AnyDesk or TeamViewer so I can help you fix the refund issue on your account",
    "I am from your bank we noticed a fraud transaction please share the OTP so we can reverse it immediately",
    "Do not tell anyone about this call it is confidential keep it secret from your family and bank",
    "Scan this QR code to receive your refund of two thousand rupees instantly",
    "Send money to this UPI collect request to verify your account ownership",
    "Work from home job guaranteed income of fifty thousand per month just pay a small registration fee",
    "Double your money in 30 days guaranteed returns investment scheme limited slots available",
    "Your electricity bill is overdue and will be disconnected tonight pay immediately through this link",
    "Click this link to verify your PAN card before midnight or your bank account gets frozen bit.ly/verify",
    "This is income tax department there is a case against you pay penalty immediately or face legal action today",
    "Your SIM card will be deactivated in two hours share the OTP received on your phone to keep it active",
    "I am calling from Amazon your order has a refund pending share your UPI pin to receive it",
    "Hello maam this is customs department your international parcel has illegal items pay fine to release it",
    "We are from TRAI your mobile number will be disconnected due to complaints press 9 to talk to an officer",
    "Your credit card limit has been increased click here to activate the offer before it expires tinyurl.com/offer",
    "This is a legal notice a case has been filed against you under section 420 pay compensation to withdraw it",
    "Share your net banking password so our technical team can fix the pending transaction issue",
    "Your electricity kyc is pending share aadhaar and otp now or power will be cut in one hour",
    "Get rich quick guaranteed profit trading crypto join our telegram group and deposit money to start",
    "This is an ED officer your bank account is linked to money laundering install remote access app now",
    "I am police inspector your son is in an accident case send money urgently to this account do not call anyone",
    "Your Netflix subscription payment failed update your card details on this link immediately netflix-billing.tk",
    "Digital arrest order issued stay on video call do not disconnect until payment is made",
]

NOT_SCAM_EXAMPLES = [
    "Hi are we still on for lunch tomorrow at the usual place",
    "Can you send me the meeting notes from yesterday's call",
    "Happy birthday hope you have a wonderful day surrounded by loved ones",
    "The train is running fifteen minutes late please wait at the platform",
    "Reminder your appointment with Dr Sharma is scheduled for 4pm on Friday",
    "Thanks for the quick delivery the package arrived in good condition",
    "Let's catch up over coffee this weekend if you are free",
    "Your electricity bill for this month is 1240 rupees due by the 15th",
    "The school will remain closed tomorrow due to heavy rainfall",
    "Please find attached the invoice for last month's work as discussed",
    "Mom I reached home safely will call you after dinner",
    "Your flight PNR is confirmed check in opens 48 hours before departure",
    "Great presentation today the client seemed really impressed",
    "Can you pick up milk and bread on your way back home",
    "The plumber will come between 10 and 11 tomorrow morning",
    "Congratulations on your promotion you truly deserved it",
    "Your order has been shipped and will arrive in 3 to 5 business days",
    "Let's plan the trip itinerary this weekend I found some good hotels",
    "The society meeting is postponed to next Sunday at 6pm",
    "Please review the attached document and share your feedback by Friday",
    "I finished the report can you go through it before we send it out",
    "Your gym membership renewal is due next week no rush",
    "We should watch that new movie everyone is talking about",
    "The doctor said it's nothing serious just rest for two days",
    "Can we reschedule our call to 3pm instead of 2pm today",
    "Your library book is due for return this Friday",
    "Thank you for the birthday wishes it means a lot to me",
    "The internet was down for an hour but it's working fine now",
    "Let's split the bill for dinner I'll send you my share",
    "Your salary has been credited to your account for this month",
]


def get_training_pairs() -> tuple[list[str], list[int]]:
    """Return (texts, labels) ready for vectorization; 1=scam, 0=not scam."""
    texts = SCAM_EXAMPLES + NOT_SCAM_EXAMPLES
    labels = [1] * len(SCAM_EXAMPLES) + [0] * len(NOT_SCAM_EXAMPLES)
    return texts, labels