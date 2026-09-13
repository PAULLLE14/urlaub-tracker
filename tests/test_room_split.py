from app.sources.room_split import candidate_allocations, cheapest_allocation, room_split


def test_room_split_baseline_unchanged():
    assert room_split(8, 3, 3) == {3: 2, 2: 1}


def test_candidate_allocations_includes_3plus3plus2_and_rounded_3x3():
    # 13.09.26 Nutzervorgabe: 2x3+1x2 ist nicht automatisch am guenstigsten.
    allocs = candidate_allocations(8, 3)
    assert {3: 2, 2: 1} in allocs          # Standard-Split
    assert {3: 3} in allocs                # aufgerundet: 3 Zimmer a 3 (9 Kapazitaet)
    assert {2: 4} in allocs                # 4 Zimmer a 2
    # jede Allokation muss tatsaechlich >= 8 Personen Platz bieten
    for a in allocs:
        assert sum(size * count for size, count in a.items()) >= 8


def test_cheapest_allocation_prefers_rounded_when_extra_room_is_cheaper():
    # Ein drittes "1 Zimmer/3 Erw."-Zimmer (auch nur mit 2 belegt) ist
    # guenstiger als 1x "1 Zimmer/2 Erw." + Aufpreis fuer die 3. Person in
    # einem der 3er-Zimmer waere in diesem Preisbeispiel nicht relevant -
    # hier einfach: 3x1500 (=4500) < 2x1500 + 1x2000 (=5000).
    allocs = candidate_allocations(8, 3)
    prices = {3: 1500.0, 2: 2000.0}
    result = cheapest_allocation(allocs, prices)
    assert result is not None
    alloc, total = result
    assert alloc == {3: 3}
    assert total == 4500.0


def test_cheapest_allocation_falls_back_to_standard_split_when_cheaper():
    # p2 < p3 < 1.5*p2 -> weder "alle Zimmer aufrunden" (3x3) noch "4x2"
    # schlaegt den Standard-Split (2x3 + 1x2).
    allocs = candidate_allocations(8, 3)
    prices = {3: 1200.0, 2: 1000.0}
    alloc, total = cheapest_allocation(allocs, prices)
    assert alloc == {3: 2, 2: 1}
    assert total == 1200.0 * 2 + 1000.0


def test_cheapest_allocation_none_when_prices_missing():
    allocs = candidate_allocations(8, 3)
    assert cheapest_allocation(allocs, {}) is None
