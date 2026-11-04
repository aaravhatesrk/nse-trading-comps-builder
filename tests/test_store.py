from datetime import datetime, timedelta

from helper.models import Profile, profile_from_dict, profile_to_dict
from helper.store import ApprovalStore

T0 = datetime(2026, 11, 4, 12, 0, 0)


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now


def test_profile_round_trips_through_json_safe_dict():
    from datetime import date
    p = Profile(symbol="MARICO", name="Marico", listing_date=date(1996, 5, 1), market_cap_cr=115000.5, roce=None)
    data = profile_to_dict(p)
    assert data["listing_date"] == "1996-05-01"
    assert profile_from_dict(data) == p


def test_create_then_get_returns_payload(tmp_path):
    store = ApprovalStore(tmp_path, clock=Clock())
    rid = store.create({"email": "a@example.com"})
    req = store.get(rid)
    assert req.payload == {"email": "a@example.com"} and not req.submitted and not req.expired


def test_unknown_id_returns_none(tmp_path):
    assert ApprovalStore(tmp_path).get("nope") is None
    assert ApprovalStore(tmp_path).get("../etc/passwd") is None


def test_request_survives_a_new_store_instance(tmp_path):
    rid = ApprovalStore(tmp_path, clock=Clock()).create({"x": 1})
    assert ApprovalStore(tmp_path, clock=Clock()).get(rid).payload == {"x": 1}


def test_request_expires_after_24h(tmp_path):
    clock = Clock()
    store = ApprovalStore(tmp_path, clock=clock)
    rid = store.create({})
    clock.now = T0 + timedelta(hours=23, minutes=59)
    assert not store.get(rid).expired
    clock.now = T0 + timedelta(hours=24, minutes=1)
    assert store.get(rid).expired
    assert store.submit(rid, ["A"]) is False


def test_double_submit_rejected(tmp_path):
    store = ApprovalStore(tmp_path, clock=Clock())
    rid = store.create({})
    assert store.submit(rid, ["A", "B"]) is True
    assert store.submit(rid, ["A"]) is False
    assert store.get(rid).chosen == ("A", "B")


def test_mark_built_records_path(tmp_path):
    store = ApprovalStore(tmp_path, clock=Clock())
    rid = store.create({})
    store.mark_built(rid, "C:/out/file.xlsx")
    assert store.get(rid).built_path == "C:/out/file.xlsx"
