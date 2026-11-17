"""Isolated API-credit Codex transport and backend; no UI imports (#661).

Token telemetry is consumed before the normal event queue coalesces it. Missing,
inconsistent or interrupted usage never releases an unaccounted reservation.
"""
from dataclasses import dataclass
import json
import threading
from decimal import Decimal, ROUND_CEILING

from .. import platform_paths
from ..billing import scrub, cost_words as shared_cost_words
from ..openai_prices import require_price, estimate, PRICE_DATE
from ..spend_ledger import default_ledger, SpendRefused
from .codex import CodexProvider
from .codex_execution import CodexExecutionConnection
from .codex_rpc import CodexRpcError, CodexTurnRejected, ApiCreditsRefused
from .codex_billing import account_matches, config_matches, route_config

COUNTERS = ('inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens')
_BASELINES_LOCK = threading.RLock()


@dataclass
class Payment:
    reservation: object
    model: str
    baseline: dict | None
    turn_id: str = ''
    cost: float = 0
    usage: bool = False
    uncertain: bool = False
    queued: bool = False
    sent: bool = False
    stopping: bool = False
    terminal: str = ''
    timer: object = None
    stop_timer: object = None
    refused: bool = False
    accounted: float = 0
    model_unknown: bool = False
    inconsistent: bool = False
    context_window: int | None = None
    timed_out: bool = False
    reported: bool = False


class CodexApiConnection(CodexExecutionConnection):
    provider_id = 'codex-api'
    billing_route = 'api'
    STOP_TIMEOUT = 5
    METHODS = CodexExecutionConnection.METHODS | {'account/login/start'}

    def __init__(self, executable=None, *, ledger=None, on_event=lambda e: None,
                 key_loader=None, home=None, **kwargs):
        self.ledger = ledger if ledger is not None else default_ledger('openai')
        self._api_event = on_event
        self._key_loader = key_loader or (lambda: platform_paths.load_secret(platform_paths.OPENAI_API_KEY_SECRET))
        self._api_logged_in = False
        self._payments_lock = threading.RLock()
        self._payments = {}
        self._api_draining = False
        self._totals = {}
        self._baseline_path = self.ledger.folder / 'codex-token-baselines.json'
        try:
            raw = json.loads(self._baseline_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                self._totals = {k: v for k, v in raw.items() if self._valid_counters(v)}
        except (OSError, ValueError):
            pass  # unknown baseline holds the reservation, never guesses
        if 'start' not in kwargs:
            kwargs['start'] = lambda exe: platform_paths.start_codex_api_server(exe, home=home)
        super().__init__(executable, **kwargs)

    def _initialize(self):
        super()._initialize()
        try:
            config = self.request('config/read', {'includeLayers': False}).get('config')
            if not config_matches(self, config):
                raise CodexRpcError('Codex could not verify ephemeral OpenAI API settings.')
            key = self._key_loader()
            if not isinstance(key, str) or not key.strip():
                raise CodexRpcError('Add an OpenAI API key in Tools, API Keys before sending.')
            # Neither key nor login request is retained on this object.
            result = super()._request('account/login/start', {'type': 'apiKey', 'apiKey': key.strip()})
            del key
            if result.get('type') != 'apiKey' or not account_matches(self, self.request('account/read', {})):
                raise CodexRpcError('Codex could not confirm OpenAI API-key sign-in.')
            self._api_logged_in = True
        except Exception:
            self.close()
            raise CodexRpcError('OpenAI API-key sign-in could not be verified. Nothing was sent.') from None

    @staticmethod
    def _valid_counters(value):
        return (isinstance(value, dict)
                and all(type(value.get(k)) is int and 0 <= value[k] <= 2**63-1 for k in COUNTERS)
                and value['cachedInputTokens'] <= value['inputTokens']
                and value['reasoningOutputTokens'] <= value['outputTokens']
                and value.get('cacheWriteInputTokens', 0) == 0)

    def _emit_api(self, kind, tid, **data):
        from ..claude_cli import TurnEvent
        try:
            self._api_event(TurnEvent(kind, session_id=tid, data={'billing': 'api',
                'provider': 'openai', 'estimated': True, **data}))
        except Exception:
            pass  # observer failure cannot strand transport or accounting

    @staticmethod
    def _require_price(model, service_tier=None):
        from ..openai_prices import Unpriced
        try:
            return require_price(model, service_tier)
        except Unpriced as error:
            raise ApiCreditsRefused(str(error)) from None

    def request(self, method, params):
        from .codex_features import ACTION_METHODS
        if method in ACTION_METHODS - {'thread/backgroundTerminals/terminate'}:
            raise ApiCreditsRefused('No API-credit price is known for ' + self._service_name(method) + '. Nothing was sent.')
        return super().request(method, params)

    def _request(self, method, params, on_sent=None, on_written=None):
        if method == 'account/login/start':
            raise CodexRpcError('Only the API backend may supply its credential.')
        params = dict(params)
        from .codex_features import ACTION_METHODS
        if method in ACTION_METHODS - {'thread/backgroundTerminals/terminate'}:
            raise ApiCreditsRefused('No API-credit price is known for ' + self._service_name(method) + '. Nothing was sent.')
        if method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start'):
            config = dict(params.get('config') or {})
            invariants = route_config(self)
            for key in ('model_provider', 'openai_base_url', 'forced_login_method', 'cli_auth_credentials_store'):
                if key in config and config[key] != invariants[key]:
                    raise ApiCreditsRefused('Codex API-credit settings point to another billing route. Nothing was sent.')
            if params.get('modelProvider', 'openai') != 'openai':
                raise CodexTurnRejected('provider')
            # Final supported lifecycle config. CLI 0.163 ignores this field
            # on turn/start; there it is only a transport validation invariant.
            config.update(invariants)
            params['config'] = config
            if method != 'turn/start':
                if not self._api_logged_in:
                    raise CodexTurnRejected('billing_route')
                if method in ('thread/resume', 'thread/fork'):
                    self._verify_saved_route(params['threadId'])
                if method == 'thread/fork' and (params.get('lastTurnId') or params.get('beforeTurnId')
                                                or params.get('threadId') not in self._totals):
                    raise ApiCreditsRefused('The API-credit cost baseline for this copy is not known. Nothing was sent.')
                self._require_price(params.get('model'))
        if method not in ('turn/start', 'turn/steer'):
            if method == 'turn/interrupt':
                with self._payments_lock:
                    payment = self._payments.get(params.get('threadId'))
                    if payment is None or payment.terminal:
                        return {}  # already ended; a late Stop is idempotent
                    if payment and not payment.terminal:
                        payment.stopping = True
                        if payment.stop_timer:
                            payment.stop_timer.cancel()
                        # Before waiting on the RPC: an unanswered interrupt
                        # must still end the process and retain its unknown cost.
                        payment.stop_timer = threading.Timer(self.STOP_TIMEOUT, self._stop_timeout, (params.get('threadId'), payment))
                        payment.stop_timer.daemon = True
                        payment.stop_timer.start()
            result = super()._request(method, params, on_sent=on_sent, on_written=on_written)
            if method == 'thread/start':
                tid = result.get('thread', {}).get('id')
                if tid:
                    self._totals[tid] = dict.fromkeys(COUNTERS, 0)
                    self._persist_baseline(tid, self._totals[tid])
            if method == 'thread/fork':
                tid = result.get('thread', {}).get('id')
                source = self._totals.get(params.get('threadId'))
                if tid and source is not None:
                    self._totals[tid] = dict(source)
                    self._persist_baseline(tid, source)
            if method in ('thread/start', 'thread/resume', 'thread/fork'):
                self._require_price(result.get('model'), result.get('serviceTier'))
            return result
        if not self._api_logged_in:
            raise CodexTurnRejected('billing_route')
        tid = params.get('threadId')
        if method == 'turn/steer':
            # A steer shares the running turn's budget, never creates fresh credit.
            with self._payments_lock:
                payment = self._payments.get(tid)
                if payment is None or payment.stopping or payment.cost >= payment.reservation.amount:
                    raise CodexTurnRejected('billing_route')
            return super()._request(method, params, on_sent=on_sent,
                on_written=lambda: self._emit_api('api_sent', tid, sent=True, kind_of_send='steer'))
        model = params.get('model')
        self._require_price(model)
        with self._payments_lock:
            if self._api_draining:
                raise ApiCreditsRefused('The API connection is stopping. Send again after it closes. Nothing was sent.')
            previous = self._payments.get(tid)
            if previous and previous.terminal:
                self._finish_payment(tid, previous)
            elif previous:
                raise CodexTurnRejected('not_idle')
            if self._api_draining:
                raise ApiCreditsRefused('The API connection is stopping. Send again after it closes. Nothing was sent.')
            try:
                reservation = self.ledger.reserve('codex-api:' + tid)
            except SpendRefused as error:
                raise ApiCreditsRefused(str(error)) from None
            # Invalidate the durable baseline BEFORE a possible paid write.
            # A crash must not reuse pre-turn counters after unknown spending.
            try:
                self._persist_baseline(tid, None)
            except OSError:
                self.ledger.close(reservation, complete=True)
                raise ApiCreditsRefused('The API-credit token baseline could not be recorded. Nothing was sent.') from None
            payment = Payment(reservation, model, self._totals.get(tid))
            self._payments[tid] = payment

        def queued():
            payment.queued = True
            if on_sent:
                on_sent()

        def written():
            payment.sent = True
            self._emit_api('api_sent', tid, sent=True)
            if on_written:
                on_written()

        try:
            result = super()._request(method, params, on_sent=queued, on_written=written)
            payment.turn_id = result.get('turn', {}).get('id', '')
            return result
        except Exception:
            with self._payments_lock:
                if self._payments.get(tid) is payment:
                    self.ledger.close(reservation, complete=not payment.queued)
                    # No usage was observed; a later matching total/last report
                    # can recover the baseline. Don't strand this chat.
                    self._payments.pop(tid, None)
            raise

    @staticmethod
    def _service_name(method):
        return {'thread/compact/start': 'conversation compaction', 'thread/goal/set': 'background goals',
                'thread/goal/clear': 'background goals', 'thread/memory/reset': 'memory'}.get(method,
                    'this background Codex service')

    def _stop_timeout(self, tid, payment):
        with self._payments_lock:
            if self._payments.get(tid) is not payment:
                return
            payment.uncertain = True
            others = any(p is not payment and not p.timed_out for p in self._payments.values())
            if not others:
                self._api_draining = True
            payment.timed_out = others
            self._finish_payment(tid, payment, keep=others)
        if not others:
            try:
                self.close()
            except CodexRpcError:
                pass

    def _verify_saved_route(self, tid):
        thread = self.request('thread/read', {'threadId': tid, 'includeTurns': False}).get('thread')
        if not isinstance(thread, dict) or thread.get('modelProvider') != 'openai':
            raise CodexTurnRejected('provider')
        config = self.request('config/read', {'cwd': thread.get('cwd'), 'includeLayers': True}).get('config')
        if not config_matches(self, config):
            raise CodexTurnRejected('billing_route')

    def _receive(self, message):
        method = message.get('method') if isinstance(message, dict) else None
        params = message.get('params', {}) if isinstance(message, dict) else {}
        tid = params.get('threadId') if isinstance(params, dict) else None
        with self._payments_lock:
            payment = self._payments.get(tid)
            if payment is None and method == 'thread/tokenUsage/updated':
                usage = params.get('tokenUsage')
                value = usage.get('total') if isinstance(usage, dict) else None
                if self._valid_counters(value):
                    self._totals[tid] = {k: value[k] for k in COUNTERS}
            if payment:
                turn_id = params.get('turnId') or params.get('turn', {}).get('id')
                if payment.turn_id and turn_id and payment.turn_id != turn_id:
                    payment.uncertain = True
                    payment.inconsistent = True
                elif method == 'turn/started':
                    payment.turn_id = turn_id or payment.turn_id
                elif method == 'thread/tokenUsage/updated':
                    usage = params.get('tokenUsage')
                    value = usage.get('total') if isinstance(usage, dict) else None
                    if not payment.turn_id and turn_id:
                        payment.turn_id = turn_id
                    if payment.baseline is None and turn_id == payment.turn_id and self._valid_counters(value):
                        last = usage.get('last')
                        if self._valid_counters(last):
                            before = {k: value[k] - last[k] for k in COUNTERS}
                            if self._valid_counters(before):
                                payment.baseline = before
                    if payment.model_unknown or not self._valid_counters(value) or payment.baseline is None:
                        payment.uncertain = True
                        payment.inconsistent = True
                    else:
                        delta = {k: value[k]-payment.baseline[k] for k in COUNTERS}
                        try:
                            previous = self._totals.get(tid, payment.baseline)
                            if any(value[k] < previous[k] for k in COUNTERS):
                                raise ValueError('Usage counters decreased')
                            cost = estimate(payment.model, delta)
                            if cost < payment.cost:
                                raise ValueError('Usage decreased')
                            accounted = float(Decimal(str(cost)).quantize(Decimal('.000001'), rounding=ROUND_CEILING))
                            increment = max(0, accounted-payment.accounted)
                            if not self.ledger.settle(payment.reservation, increment,
                                                      {payment.model: increment}):
                                payment.uncertain = True
                            payment.accounted = accounted
                            payment.cost = cost
                            payment.usage = True
                            payment.context_window = usage.get('modelContextWindow')
                            self._totals[tid] = {k: value[k] for k in COUNTERS}
                        except ValueError:
                            payment.uncertain = True
                            payment.inconsistent = True
                    if payment.cost >= payment.reservation.amount or payment.uncertain:
                        self._interrupt_payment(tid, payment, turn_id)
                elif method == 'model/rerouted':
                    # Mixed-model counters cannot be attributed safely.
                    payment.uncertain = True
                    payment.model_unknown = True
                    payment.inconsistent = True
                    self._interrupt_payment(tid, payment, turn_id)
                elif method == 'error':
                    payment.uncertain = True  # even a successful retry can hide billed partial output
                    error = params.get('error')
                    info = error.get('codexErrorInfo') if isinstance(error, dict) else None
                    status = next((v.get('httpStatusCode') for v in info.values() if isinstance(v, dict)), None) if isinstance(info, dict) else None
                    refused = status in (401, 403) or info in ('unauthorized', 'authenticationError') if isinstance(info, str) else status in (401, 403)
                    if refused or info == 'usageLimitExceeded':
                        payment.refused = refused
                        self._emit_api('api_error', tid, failure='usage_limit' if info == 'usageLimitExceeded' else 'key_refused')
                        self._interrupt_payment(tid, payment, turn_id)
                elif method == 'turn/completed':
                    payment.terminal = params.get('turn', {}).get('status', 'unknown')
                    payment.timer = threading.Timer(.5, self._finish_payment, (tid, payment))
                    payment.timer.daemon = True
                    payment.timer.start()
        # Protocol replies, IDs, paths and approval checks are never rewritten.
        # Presentation text is scrubbed after decoding, at the event boundary.
        super()._receive(message)

    def _interrupt_payment(self, tid, payment, turn_id):
        if payment.stopping or not turn_id:
            return
        payment.stopping = True
        def interrupt():
            try:
                self.request('turn/interrupt', {'threadId': tid, 'turnId': turn_id})
            except Exception:
                self._stop_timeout(tid, payment)
        threading.Thread(target=interrupt, daemon=True).start()

    def _finish_payment(self, tid, payment, keep=False):
        with self._payments_lock:
            if self._payments.get(tid) is not payment:
                return
            if payment.timer:
                payment.timer.cancel()
            if payment.stop_timer:
                payment.stop_timer.cancel()
            complete = payment.refused or (payment.usage and not payment.uncertain and payment.terminal == 'completed')
            upper_bound = False
            if payment.stopping and not payment.uncertain and not payment.refused:
                from ..openai_prices import request_ceiling
                maximum = payment.accounted + request_ceiling(payment.model, payment.context_window)
                if maximum <= payment.reservation.amount:
                    extra = float(Decimal(str(maximum-payment.accounted)).quantize(Decimal('.000001'), rounding=ROUND_CEILING))
                    if self.ledger.settle(payment.reservation, extra, {payment.model: extra}):
                        payment.cost = payment.accounted + extra
                        payment.usage = complete = upper_bound = True
            if payment.inconsistent:
                self._totals.pop(tid, None)
            try:
                self._persist_baseline(tid, self._totals.get(tid))
            except OSError:
                complete = False
            if not keep:
                self.ledger.close(payment.reservation, complete=complete)
                self._payments.pop(tid, None)
            if not payment.reported:
                payment.reported = True
                self._emit_api('finished', tid, sent=payment.sent, cost_usd=payment.cost if payment.usage else None,
                    cost_complete=complete, cost_upper_bound=upper_bound, price_date=PRICE_DATE,
                    cost_words=self._cost_words(payment, complete, upper_bound))
            if not keep and self._payments and all(p.timed_out for p in self._payments.values()):
                self._api_draining = True
                # Only abandoned turns remain: stop them after the other chats
                # finish, while retaining their live reservations and telemetry.
                def cleanup():
                    try:
                        self.close()
                    except CodexRpcError:
                        pass
                threading.Thread(target=cleanup, daemon=True).start()

    @staticmethod
    def _cost_words(payment, complete, upper_bound):
        from ..billing import COST_NOT_KNOWN, money
        if upper_bound:
            return 'estimated, at most ' + money(payment.cost)
        words = shared_cost_words(payment.cost if payment.usage else None, complete)
        return words if words == COST_NOT_KNOWN else 'estimated ' + words

    def _persist_baseline(self, tid, value):
        from ..provider_storage import atomic_write_json
        with _BASELINES_LOCK:
            try:
                saved = json.loads(self._baseline_path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                saved = {}
            except ValueError:
                raise OSError('Invalid token baselines') from None
            if not isinstance(saved, dict):
                raise OSError('Invalid token baselines')
            if value is None:
                saved.pop(tid, None)
            else:
                saved[tid] = value
            atomic_write_json(self._baseline_path, saved)

    def _fail(self, reason):
        # Reader disconnect, write failure and process shutdown all retain holds.
        super()._fail(reason)
        with self._payments_lock:
            for tid, payment in list(self._payments.items()):
                payment.uncertain = True
                self._finish_payment(tid, payment)


class CodexApiProvider(CodexProvider):
    provider_id = 'codex-api'

    def __init__(self, *, executable=None, on_update=lambda state: None,
                 on_event=lambda event: None, ledger=None, key_loader=None, home=None):
        from .codex_runtime import CodexChatRuntime
        from .codex_connection import ManagedCodexConnection
        factory = lambda exe: CodexApiConnection(exe, initialize=False, ledger=ledger,
            on_event=on_event, key_loader=key_loader, home=home)
        runtime = CodexChatRuntime(on_update, executable=executable, factory=factory)
        runtime.provider_id = self.provider_id
        from ..provider_storage import CodexPlanStore
        from .codex_permissions import CodexPermissionStore, CodexReviewStore
        runtime.plan_store = CodexPlanStore(platform_paths.app_data_dir() / 'provider-tasks' / self.provider_id)
        permissions = platform_paths.app_data_dir() / 'provider-permissions' / self.provider_id
        runtime.permission_store = CodexPermissionStore(permissions)
        runtime.review_store = CodexReviewStore(permissions)
        connection = ManagedCodexConnection(executable, factory=factory)
        self.ledger = ledger if ledger is not None else default_ledger('openai')
        super().__init__(connection, runtime=runtime)

    def capabilities(self, session):
        from dataclasses import replace
        # Side questions still use the subscription-only aside runner. Never
        # advertise that path for API chats until it has paid accounting.
        return replace(super().capabilities(session), aside=False)

    def create_owned(self, cwd, title, *, model='gpt-5-nano', **kwargs):
        return super().create_owned(cwd, title, model=model, **kwargs)

    def collect(self):
        from dataclasses import replace
        snapshot = super().collect()
        snapshot.sessions = [replace(info, provider=self.provider_id,
            key=f'{self.provider_id}:{info.cli_session_id}', api_credits=True) for info in snapshot.sessions]
        return snapshot

    def usage_lines(self, session):
        self._check(session)
        totals = self.ledger.totals()
        return [f'OpenAI API credits: estimated spending ${totals.spent:.6f} of ${totals.limits.overall:.2f}.',
                f'Held ${totals.held:.2f}; estimated remaining ${totals.left:.6f}.',
                'Limits are approximate; one request can pass a limit.']

    def usage_warnings(self, session):
        self._check(session)
        return []
