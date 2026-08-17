"""Row DTOs — the persistence shape, and nothing else knows it.

One frozen dataclass per table, columns verbatim. Nothing here has a method, a
default, or a validator, and nothing here appears in a signature outside this
package: a row crosses into `mapper/`, becomes a model object, and stops.
"""
