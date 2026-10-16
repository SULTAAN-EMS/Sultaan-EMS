def seat_row_placement(row_number, row_count):
    """Return the invigilator-facing side and PDF grid column for a row."""
    row_count = max(1, int(row_count or 1))
    row_number = max(0, int(row_number or 0))

    if row_number == 0:
        return "MIDIG", 1
    if row_number >= row_count - 1:
        return "BIDIX", 3
    return "DHEXE", 2
