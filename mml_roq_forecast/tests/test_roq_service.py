"""Tests for ROQService."""


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _FakeNullService:
    """Mimics NullService: all calls return None."""
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class _FakeFreightService:
    """Mimics a FreightService with a single known booking."""
    def __init__(self, known_booking_id, transit_days, partner_id=None):
        self._known = {known_booking_id: transit_days}
        self._partner_ids = {known_booking_id: partner_id} if partner_id is not None else {}

    def get_booking_lead_time(self, booking_id):
        return self._known.get(booking_id)

    def get_booking_supplier_partner_id(self, booking_id):
        return self._partner_ids.get(booking_id)


class _Namespace:
    """Simple attribute bag."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeRegistry:
    """Minimal env['mml.registry'] stand-in."""
    def __init__(self, service_obj):
        self._svc = service_obj

    def service(self, _name):
        return self._svc


class _FakePartnerRecord:
    """Fake res.partner record with exists() and action_update_lead_time_stats()."""
    def __init__(self, on_update=None):
        self._on_update = on_update

    def exists(self):
        return True

    def action_update_lead_time_stats(self):
        if self._on_update is not None:
            self._on_update()


class _FakePartnerModel:
    def __init__(self, partner_record):
        self._partner = partner_record

    def browse(self, partner_id):
        return self._partner


class _FakeEnv:
    def __init__(self, registry_svc, booking=None, partner=None):
        self._registry = _FakeRegistry(registry_svc)
        self._booking = booking
        self._partner = partner

    def __getitem__(self, model):
        if model == 'mml.registry':
            return self._registry
        if model == 'freight.booking':
            return self
        if model == 'res.partner':
            return _FakePartnerModel(self._partner)
        raise KeyError(model)

    def browse(self, booking_id):
        return self._booking


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

def test_roq_service_importable():
    from mml_roq_forecast.services.roq_service import ROQService
    assert ROQService is not None


def test_roq_service_has_on_freight_booking_confirmed():
    from mml_roq_forecast.services.roq_service import ROQService
    assert callable(getattr(ROQService, 'on_freight_booking_confirmed', None))


def test_roq_service_constructor_stores_env():
    from mml_roq_forecast.services.roq_service import ROQService
    svc = ROQService(_FakeEnv(_FakeNullService()))
    assert svc.env is not None


# ---------------------------------------------------------------------------
# Behavioural tests
# ---------------------------------------------------------------------------

def test_exits_early_when_res_id_is_none():
    """Handler must return immediately when event has no res_id."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class TrackingPartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    svc = ROQService(_FakeEnv(_FakeNullService()))
    event = _Namespace(res_id=None)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when res_id is None"


def test_exits_early_when_freight_service_returns_none():
    """Handler must return when get_booking_lead_time returns None (booking missing or mml_freight uninstalled)."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []

    class TrackingPartner:
        def action_update_lead_time_stats(self):
            update_called.append(True)

    # NullService returns None for all calls
    env = _FakeEnv(_FakeNullService())
    svc = ROQService(env)
    event = _Namespace(res_id=42)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when transit_days is None"


def test_exits_early_when_booking_has_no_purchase_order():
    """Handler must not call action_update_lead_time_stats when freight service returns no partner."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []
    # partner_id=None → get_booking_supplier_partner_id returns None → early exit
    env = _FakeEnv(_FakeFreightService(known_booking_id=42, transit_days=14.0, partner_id=None))
    svc = ROQService(env)
    event = _Namespace(res_id=42)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [], "action_update_lead_time_stats must not be called when partner_id is None"


def test_calls_action_update_lead_time_stats_on_happy_path():
    """Handler must call action_update_lead_time_stats on the booking's supplier partner."""
    from mml_roq_forecast.services.roq_service import ROQService

    update_called = []
    fake_partner = _FakePartnerRecord(on_update=lambda: update_called.append(True))
    env = _FakeEnv(
        _FakeFreightService(known_booking_id=99, transit_days=21.0, partner_id=1),
        partner=fake_partner,
    )
    svc = ROQService(env)
    event = _Namespace(res_id=99)
    svc.on_freight_booking_confirmed(event)
    assert update_called == [True], "action_update_lead_time_stats must be called on the supplier partner"
