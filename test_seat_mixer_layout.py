from app.seat_mixer_layout import seat_row_placement


def test_two_rows_face_right_then_left():
    assert [seat_row_placement(row, 2) for row in range(2)] == [
        ("MIDIG", 1),
        ("BIDIX", 3),
    ]


def test_three_rows_face_right_middle_left():
    assert [seat_row_placement(row, 3) for row in range(3)] == [
        ("MIDIG", 1),
        ("DHEXE", 2),
        ("BIDIX", 3),
    ]


def test_four_and_five_rows_keep_intermediate_rows_in_middle():
    assert [seat_row_placement(row, 4) for row in range(4)] == [
        ("MIDIG", 1),
        ("DHEXE", 2),
        ("DHEXE", 2),
        ("BIDIX", 3),
    ]
    assert [seat_row_placement(row, 5) for row in range(5)] == [
        ("MIDIG", 1),
        ("DHEXE", 2),
        ("DHEXE", 2),
        ("DHEXE", 2),
        ("BIDIX", 3),
    ]


def test_single_row_is_on_invigilators_right():
    assert seat_row_placement(0, 1) == ("MIDIG", 1)
