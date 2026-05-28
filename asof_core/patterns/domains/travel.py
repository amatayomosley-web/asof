"""Travel domain pack.

Travel-pricing vocabulary. Fares and availability shift on hour-to-hour
scales for the same booking.
"""

PATTERNS: list[tuple[str, str]] = [
    (
        "fare_or_rate",
        r"\b(fare|airfare|hotel\s+rate|room\s+rate|nightly\s+rate|daily\s+rate|tariff)\b",
    ),
    (
        "availability",
        r"\b(availability|available\s+rooms|seats?\s+available|sold\s+out|fully\s+booked)\b",
    ),
    (
        "booking_class",
        r"\b(economy|business\s+class|first\s+class|premium\s+economy|coach|fare\s+class)\b",
    ),
    (
        "route_or_destination",
        r"\b(round[-\s]?trip|one[-\s]?way|connecting\s+flight|direct\s+flight|nonstop)\b",
    ),
    (
        "airline_codes_with_pricing",
        r"\b(UA|AA|DL|BA|AF|LH|EK|QF|JL|NH)\s*\d+\b",
    ),
    (
        "lodging_chains",
        r"\b(Hilton|Marriott|Hyatt|IHG|AirBnB|Vrbo|Booking\.com|Expedia)\b",
    ),
]
