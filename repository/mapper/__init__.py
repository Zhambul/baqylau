"""Row DTO to model object, and back. Pure functions.

No I/O, no SQL, no clock, no driver — which is what makes every encoding and
validation decision in the storage layer testable without a database. All of it
used to be inline in the store classes, and three pieces of it existed twice.
"""
