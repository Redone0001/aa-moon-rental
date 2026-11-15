from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection

from moon_rental.models import Bid, Rental
from moon_rental.services.rentals import place_bid, settle_auction

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.concurrency]


def race(call):
    barrier = Barrier(2)

    def worker():
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            return call()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        return [future.result(timeout=30) for future in futures]


def test_simultaneous_equal_bids_are_serialized(auction, pilot):
    if not connection.features.has_select_for_update:
        pytest.skip("SQLite cannot prove row-lock concurrency; CI runs this on MySQL.")
    pilot.has_perm("moon_rental.basic_access")
    lot_id = auction.lots.get().pk

    def bid():
        try:
            place_bid(actor=pilot, lot_id=lot_id, amount="150")
            return "accepted"
        except ValidationError:
            return "rejected"

    assert sorted(race(bid)) == ["accepted", "rejected"]
    assert Bid.objects.count() == 1


def test_simultaneous_settlement_only_awards_once(auction, pilot):
    if not connection.features.has_select_for_update:
        pytest.skip("SQLite cannot prove row-lock concurrency; CI runs this on MySQL.")
    place_bid(actor=pilot, lot_id=auction.lots.get().pk, amount="150")
    assert sorted(race(lambda: settle_auction(auction.pk, at=auction.ends_at))) == [0, 1]
    assert Rental.objects.count() == 1
