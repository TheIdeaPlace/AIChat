"""Isolated API-credit Codex transport and backend; no UI imports (#661).

Token telemetry is consumed before the normal event queue coalesces it. Missing,
inconsistent or interrupted usage never releases an unaccounted reservation.
"""
from dataclasses import dataclass, field
import json
import threading
from decimal import Decimal, ROUND_CEILING

from .. import platform_paths, openai_prices
from ..billing import scrub, cost_words as shared_cost_words
from ..openai_prices import is_priced, valid_counters, estimate, PRICE_DATE
from ..spend_ledger import default_ledger, SpendRefused
from .codex import CodexProvider
from .codex_execution import CodexExecutionConnection
from .codex_rpc import CodexRpcError, CodexTurnRejected, ApiCreditsRefused
from .codex_billing import account_matches, config_matches, route_config

COUNTERS = ('inputTokens', 'cachedInputTokens', 'outputTokens', 'reasoningOutputTokens', 'cacheWriteInputTokens')
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
    upper_bound: bool = False
    last_total: dict | None = None
    ordinal: int = 0
    priced: bool = True
    tokens: dict | None = None
    priced_on: object = None
    steer_allowances: list = field(default_factory=list)
    priced_tokens: dict | None = None


class CodexApiConnection(CodexExecutionConnection):
    provider_id = 'codex-api'
    billing_route = 'api'
    STOP_TIMEOUT = 5
    METHODS = CodexExecutionConnection.METHODS | {'account/login/start'}

    def __init__(self, executable=None, *, ledger=None, on_event=lambda e: None,
                 key_loader=None, home=None, purpose='turn', **kwargs):
        self.ledger = ledger if ledger is not None else default_ledger('openai')
        self._has_custom_key = key_loader is not None
        self._api_event = on_event
        self._purpose = purpose
        self._key_loader = key_loader or (lambda: platform_paths.load_secret(platform_paths.OPENAI_API_KEY_SECRET))
        self._api_logged_in = False
        self._credential_generation = None
        self._payments_lock = threading.RLock()
        self._payments = {}
        self._past_payments = {}
        self._unpriced_allowed = set()
        self._payment_number = 0
        self._ephemeral_threads = set()
        self._api_draining = False
        self._totals = {}
        self._baseline_path = self.ledger.folder / 'codex-token-baselines.json'
        try:
            raw = json.loads(self._baseline_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                self._totals = {k: {field: v.get(field, 0) for field in COUNTERS}
                                for k, v in raw.items() if self._valid_counters(v)}
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
            from ..openai_billing import credentials_lock
            with credentials_lock:
                self._login_api()
        except Exception:
            self.close()
            raise CodexRpcError('OpenAI API-key sign-in could not be verified. Nothing was sent.') from None

    def _login_api(self):
        from ..openai_billing import key_generation
        key = self._key_loader()
        if not isinstance(key, str) or not key.strip():
            self._api_logged_in = False
            raise ApiCreditsRefused('Add an OpenAI API key in Tools, API Keys before sending. Nothing was sent.')
        result = super()._request('account/login/start', {'type': 'apiKey', 'apiKey': key.strip()})
        del key
        if result.get('type') != 'apiKey' or not account_matches(self, self.request('account/read', {})):
            self._api_logged_in = False
            raise CodexRpcError('Codex could not confirm OpenAI API-key sign-in.')
        self._api_logged_in = True
        self._credential_generation = key_generation()

    @staticmethod
    def _valid_counters(value):
        return valid_counters(value)

    def authorize_unpriced(self, thread_id, model, tier='default'):
        """Record an explicit answer for this exact connection and selection."""
        self._require_price(model, tier)
        with self._runtime_lock:
            proof = self._runtime_threads.get(thread_id)
        if proof is None or proof.model != model or not self.owns_runtime(proof):
            raise ApiCreditsRefused('The API model changed. Review its price before sending. Nothing was sent.')
        if tier != 'default':
            raise ApiCreditsRefused('API credits require standard service. Nothing was sent.')
        with self._payments_lock:
            self._unpriced_allowed = {choice for choice in self._unpriced_allowed if choice[0] != thread_id}
            self._unpriced_allowed.add((thread_id, model, tier))

    def _accept_owned_thread(self, proof):
        with self._runtime_lock:
            previous = self._runtime_threads.get(proof.native_id)
        super()._accept_owned_thread(proof)
        if previous is not None and previous.model != proof.model:
            with self._payments_lock:
                self._unpriced_allowed = {choice for choice in self._unpriced_allowed if choice[0] != proof.native_id}

    def _check_unpriced_consent(self, thread_id, model, tier='default', *, as_of=None):
        if not is_priced(model, tier, as_of=as_of) and (thread_id, model, tier) not in self._unpriced_allowed:
            raise ApiCreditsRefused(f'Price unknown for {model}. Confirm the price-unknown question before sending. Nothing was sent.')

    def accept_key_test(self, proof):
        # Like the separate aside executor, but only for a tiny paid key test.
        # Ordinary ephemeral permission probes remain non-executable.
        if (self._purpose != 'test' or proof.connection is not self or not proof.ephemeral
                or proof.model != 'gpt-5-nano' or proof.allowed_tools
                or proof.policy.sandbox != 'read-only' or proof.policy.approval != 'never'
                or proof.policy.reviewer != 'user'):
            raise CodexRpcError('Invalid OpenAI key test configuration')
        with self._runtime_lock:
            if self._runtime_threads:
                raise CodexRpcError('An OpenAI key test can only run once per process')
            self._runtime_threads[proof.native_id] = proof

    def _emit_api(self, kind, tid, **data):
        from ..claude_cli import TurnEvent
        try:
            self._api_event(TurnEvent(kind, session_id=tid, data={'billing': 'api',
                'provider': 'openai', 'estimated': True, **data}))
        except Exception:
            pass  # observer failure cannot strand transport or accounting

    @staticmethod
    def _require_price(model, service_tier=None):
        if not isinstance(model, str) or not model.strip():
            raise ApiCreditsRefused('Choose an API-credit model. Nothing was sent.')
        if service_tier not in (None, 'default', 'standard'):
            raise ApiCreditsRefused('API credits require standard service. Nothing was sent.')
        return is_priced(model, service_tier)

    def request(self, method, params):
        from .codex_features import ACTION_METHODS
        if method in ACTION_METHODS - {'thread/backgroundTerminals/terminate'}:
            raise ApiCreditsRefused('No API-credit price is known for ' + self._service_name(method) + '. Nothing was sent.')
        return super().request(method, params)

    def _request(self, method, params, on_sent=None, on_written=None):
        if method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start', 'turn/steer'):
            from ..openai_billing import credentials_lock, key_generation
            # Serialize key changes with preflight and the actual paid write,
            # including automatically advanced queued messages.
            with credentials_lock:
                if not self._api_logged_in or self._credential_generation != key_generation():
                    self._login_api()
                return self._request_api(method, params, on_sent, on_written)
        return self._request_api(method, params, on_sent, on_written)

    def _request_api(self, method, params, on_sent=None, on_written=None):
        if method == 'account/login/start':
            raise CodexRpcError('Only the API backend may supply its credential.')
        params = dict(params)
        from .codex_features import ACTION_METHODS
        if method in ACTION_METHODS - {'thread/backgroundTerminals/terminate'}:
            raise ApiCreditsRefused('No API-credit price is known for ' + self._service_name(method) + '. Nothing was sent.')
        if method in ('thread/start', 'thread/resume', 'thread/fork', 'turn/start'):
            if params.get('effort') == 'ultra' or (params.get('config') or {}).get('model_reasoning_effort') == 'ultra':
                raise ApiCreditsRefused('Automatic task delegation is unavailable on API credits. Nothing was sent.')
            if params.get('serviceTier', 'default') != 'default' or params.get('serviceTierForTurn', 'default') != 'default':
                raise ApiCreditsRefused('API credits require standard service. Nothing was sent.')
            params['serviceTier'] = 'default'
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
                if tid and params.get('ephemeral'):
                    self._ephemeral_threads.add(tid)
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
                if result.get('reasoningEffort') == 'ultra':
                    raise ApiCreditsRefused('Automatic task delegation is unavailable on API credits. Choose an explicit effort. Nothing was sent.')
            return result
        if not self._api_logged_in:
            raise CodexTurnRejected('billing_route')
        tid = params.get('threadId')
        if method == 'turn/steer':
            # Priced steers share the budget; unpriced steers hold another allowance.
            with self._payments_lock:
                payment = self._payments.get(tid)
                if payment is None or payment.stopping or payment.cost >= payment.reservation.amount:
                    raise CodexTurnRejected('billing_route')
                self._expire_payment(payment)
                self._check_unpriced_consent(tid, payment.model)
                extra = None
                if not payment.priced:
                    try:
                        extra = self.ledger.reserve('codex-api:' + tid, purpose=self._purpose, unpriced=True)
                    except SpendRefused as error:
                        raise ApiCreditsRefused(str(error)) from None
                    payment.steer_allowances.append(extra)
            queued = []
            def sent():
                queued.append(True)
                if on_sent:
                    on_sent()
            try:
                return super()._request(method, params, on_sent=sent,
                    on_written=lambda: self._emit_api('api_sent', tid, sent=True, kind_of_send='steer'))
            except Exception:
                if extra is not None and not queued:
                    self.ledger.close(extra, complete=True)
                raise
        model = params.get('model')
        self._require_price(model)
        with self._payments_lock:
            priced_on = openai_prices.pricing_time()
            priced = is_priced(model, as_of=priced_on)
            self._check_unpriced_consent(tid, model, as_of=priced_on)
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
                reservation = self.ledger.reserve('codex-api:' + tid, purpose=self._purpose, unpriced=not priced)
            except SpendRefused as error:
                raise ApiCreditsRefused(str(error)) from None
            # Invalidate the durable baseline BEFORE a possible paid write.
            # A crash must not reuse pre-turn counters after unknown spending.
            try:
                self._persist_baseline(tid, None)
            except OSError:
                self.ledger.close(reservation, complete=True)
                raise ApiCreditsRefused('The API-credit token baseline could not be recorded. Nothing was sent.') from None
            self._payment_number += 1
            payment = Payment(reservation, model, self._totals.get(tid), ordinal=self._payment_number,
                              priced=priced, priced_on=priced_on)
            self._payments[tid] = payment

        def queued():
            payment.queued = True
            if on_sent:
                on_sent()

        def written():
            with self._payments_lock:
                payment.sent = True
                # A fast reply can reach the reader before the writer's
                # callback. Never overwrite measured tokens with a placeholder.
                if not payment.priced and payment.tokens is None:
                    self.ledger.record_openai_tokens(reservation, model, dict.fromkeys(COUNTERS, 0), unpriced=True, measured=False)
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
                'thread/goal/clear': 'background goals', 'thread/memoryMode/set': 'memory'}.get(method,
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
            self._emit_api('api_error', tid, failure='stop_pending')
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
            archived = False
            if method == 'thread/tokenUsage/updated':
                past = self._past_payments.get((tid, params.get('turnId')))
                if past is not None and past is not payment:
                    payment, archived = past, True
            if payment is None:
                # Unattributed telemetry must not move a baseline without a
                # matching reservation. A later turn can recover total-last.
                pass
            if payment:
                self._expire_payment(payment)
                turn_id = params.get('turnId') or params.get('turn', {}).get('id')
                if payment.turn_id and turn_id and payment.turn_id != turn_id:
                    payment.uncertain = True
                    payment.inconsistent = True
                elif method == 'turn/started':
                    payment.turn_id = turn_id or payment.turn_id
                elif method == 'thread/tokenUsage/updated':
                    was_usage, prior_total = payment.usage, payment.last_total
                    usage = params.get('tokenUsage')
                    value = usage.get('total') if isinstance(usage, dict) else None
                    if self._valid_counters(value):
                        value = {k: value.get(k, 0) for k in COUNTERS}
                    if not payment.turn_id and turn_id:
                        payment.turn_id = turn_id
                    if payment.baseline is None and turn_id == payment.turn_id and self._valid_counters(value):
                        last = usage.get('last')
                        if self._valid_counters(last):
                            before = {k: value[k] - last.get(k, 0) for k in COUNTERS}
                            if self._valid_counters(before) and (payment.baseline is None or all(before[k] >= payment.baseline[k] for k in COUNTERS)):
                                payment.baseline = before
                    if payment.model_unknown or not self._valid_counters(value) or payment.baseline is None:
                        payment.uncertain = True
                        payment.inconsistent = True
                    else:
                        delta = {k: value[k]-payment.baseline[k] for k in COUNTERS}
                        try:
                            if not self._valid_counters(delta):
                                raise ValueError('Invalid token delta')
                            previous = payment.last_total or payment.baseline
                            if any(value[k] < previous[k] for k in COUNTERS):
                                raise ValueError('Usage counters decreased')
                            cost = estimate(payment.model, delta, as_of=payment.priced_on) if payment.priced else 0
                            if payment.priced and not payment.upper_bound and cost < payment.cost:
                                raise ValueError('Usage decreased')
                            accounted = float(Decimal(str(cost)).quantize(Decimal('.000001'), rounding=ROUND_CEILING))
                            increment = max(0, accounted-payment.accounted)
                            payment.tokens = delta
                            tokens = delta if payment.priced else self._unpriced_tokens(payment)
                            recorded = self.ledger.record_openai_tokens(payment.reservation, payment.model,
                                tokens or dict.fromkeys(COUNTERS, 0), unpriced=not payment.priced,
                                measured=tokens is not None)
                            if payment.priced:
                                recorded = self.ledger.settle(payment.reservation, increment,
                                    {payment.model: increment}) and recorded
                            if not recorded:
                                payment.uncertain = True
                            payment.accounted = max(payment.accounted, accounted)
                            payment.cost = max(cost, payment.accounted) if payment.upper_bound else cost
                            payment.usage = True
                            payment.context_window = usage.get('modelContextWindow')
                            payment.last_total = {k: value[k] for k in COUNTERS}
                            if not archived or (tid not in self._payments and all(value[k] >= self._totals.get(tid, payment.baseline)[k] for k in COUNTERS)):
                                self._totals[tid] = dict(payment.last_total)
                        except ValueError:
                            payment.uncertain = True
                            payment.inconsistent = True
                    if archived and (not was_usage or prior_total != payment.last_total):
                        if not payment.inconsistent and recorded:
                            self._adjust_successors(tid, payment)
                        self._finish_payment(tid, payment, late=True)
                    if not archived and payment.priced and payment.cost >= payment.reservation.amount and not payment.stopping:
                        self._emit_api('api_error', tid, failure='message_limit', limit=payment.reservation.amount,
                                       overall=payment.reservation.overall_cap,
                                       others_held=payment.reservation.others_held)
                    if not archived and ((payment.priced and payment.cost >= payment.reservation.amount) or payment.inconsistent):
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
                    elif not params.get('willRetry'):
                        self._emit_api('api_error', tid, failure='connection_dropped')
                elif method == 'turn/completed':
                    payment.terminal = params.get('turn', {}).get('status', 'unknown')
                    payment.timer = threading.Timer(.5, self._finish_payment, (tid, payment))
                    payment.timer.daemon = True
                    payment.timer.start()
        # Protocol replies, IDs, paths and approval checks are never rewritten.
        # Presentation text is scrubbed after decoding, at the event boundary.
        super()._receive(message)

    def _adjust_successors(self, tid, earlier):
        # A delayed final snapshot can include requests previously absorbed
        # into the next turn's cumulative delta. Charge earlier first, then
        # correct the later reservation; interruption between writes is safe.
        later = [p for (thread, _), p in self._past_payments.items() if thread == tid]
        if tid in self._payments:
            later.append(self._payments[tid])
        for payment in later:
            if payment.ordinal <= earlier.ordinal or payment.baseline is None:
                continue
            baseline = {k: max(payment.baseline[k], earlier.last_total[k]) for k in COUNTERS}
            if baseline == payment.baseline:
                continue
            payment.baseline = baseline
            if not payment.usage or payment.upper_bound:
                continue
            try:
                delta = {k: payment.last_total[k]-baseline[k] for k in COUNTERS}
                if not self._valid_counters(delta):
                    raise ValueError('Usage overlap is inconsistent')
                if payment.priced_tokens is not None:
                    removed = {k: payment.tokens[k] - delta[k] for k in COUNTERS}
                    payment.priced_tokens = {k: max(0, payment.priced_tokens[k]-removed[k]) for k in COUNTERS}
                payment.tokens = delta
                tokens = delta if payment.priced else self._unpriced_tokens(payment)
                if not self.ledger.record_openai_tokens(payment.reservation, payment.model,
                        tokens or dict.fromkeys(COUNTERS, 0), unpriced=not payment.priced, measured=tokens is not None):
                    payment.uncertain = True
                priced = delta if payment.priced else payment.priced_tokens
                cost = estimate(payment.model, priced, as_of=payment.priced_on) if priced is not None else 0
            except ValueError:
                payment.uncertain = payment.inconsistent = True
                continue
            accounted = float(Decimal(str(cost)).quantize(Decimal('.000001'), rounding=ROUND_CEILING))
            credit = max(0, payment.accounted-accounted)
            if credit and not self.ledger.correct_openai_overlap(payment.reservation, credit, payment.model):
                payment.uncertain = True
                continue
            payment.cost, payment.accounted = cost, accounted
            if payment.reported:
                self._finish_payment(tid, payment, late=True)

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

    def _finish_payment(self, tid, payment, keep=False, late=False):
        with self._payments_lock:
            if not late and self._payments.get(tid) is not payment:
                return
            self._expire_payment(payment)
            if payment.timer:
                payment.timer.cancel()
            if payment.stop_timer:
                payment.stop_timer.cancel()
            refused_without_usage = payment.refused and not payment.usage and not payment.steer_allowances
            if refused_without_usage:
                self.ledger.record_openai_tokens(payment.reservation, payment.model,
                    dict.fromkeys(COUNTERS, 0), unpriced=False)
            complete = ((payment.refused if payment.priced else refused_without_usage)
                        or (payment.priced and payment.usage and not payment.uncertain
                            and payment.terminal == 'completed'))
            upper_bound = payment.upper_bound
            if upper_bound:
                complete = True
            if payment.priced and payment.stopping and not payment.uncertain and not payment.refused and not upper_bound:
                from ..openai_prices import request_ceiling
                maximum = payment.accounted + request_ceiling(payment.model, payment.context_window, as_of=payment.priced_on)
                if maximum <= payment.reservation.amount:
                    extra = float(Decimal(str(maximum-payment.accounted)).quantize(Decimal('.000001'), rounding=ROUND_CEILING))
                    if self.ledger.settle(payment.reservation, extra, {payment.model: extra}):
                        payment.cost = payment.accounted + extra
                        payment.accounted = payment.cost
                        payment.usage = complete = upper_bound = payment.upper_bound = True
            if (payment.inconsistent or (not payment.usage and not payment.refused)) and not late:
                self._totals.pop(tid, None)
            try:
                if not late or tid not in self._payments:
                    self._persist_baseline(tid, self._totals.get(tid))
            except OSError:
                complete = False
            if not keep:
                if not payment.priced and payment.sent and not refused_without_usage:
                    tokens = self._unpriced_tokens(payment)
                    self.ledger.record_openai_tokens(payment.reservation, payment.model,
                        tokens or dict.fromkeys(COUNTERS, 0), unpriced=True,
                        measured=bool(tokens is not None and not payment.uncertain
                                      and not payment.inconsistent and payment.terminal == 'completed'))
                self.ledger.close(payment.reservation, complete=complete)
                for allowance in payment.steer_allowances:
                    # A steer's tokens are already included in the main turn's
                    # absolute snapshot. Mark that attribution without adding
                    # them again or inventing missing telemetry for each steer.
                    self.ledger.record_openai_tokens(allowance, payment.model,
                        dict.fromkeys(COUNTERS, 0), unpriced=True)
                    self.ledger.close(allowance, complete=refused_without_usage)
                if not late:
                    self._payments.pop(tid, None)
                    if payment.turn_id:
                        self._past_payments[(tid, payment.turn_id)] = payment
            if not payment.reported or late:
                payment.reported = True
                self._emit_api('finished', tid, sent=payment.sent, cost_usd=payment.cost if payment.priced and payment.usage else None,
                    unpriced=not payment.priced, tokens=payment.tokens, allowance=payment.reservation.amount,
                    turn_id=payment.turn_id, payment_id=(id(self), payment.ordinal),
                    outcome=payment.terminal,
                    cost_complete=complete, cost_upper_bound=upper_bound, price_date=PRICE_DATE,
                    cost_words=self._cost_words(payment, complete, upper_bound))
            if not late and not keep and self._payments and all(p.timed_out for p in self._payments.values()):
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
        if payment.refused and not payment.usage and not payment.steer_allowances:
            return 'nothing was charged'
        if not payment.priced:
            from ..openai_billing import token_words
            measured = (payment.usage and not payment.uncertain and not payment.inconsistent
                        and payment.terminal == 'completed')
            return (token_words(payment.tokens, complete=measured) + ', cost unknown' if payment.tokens else
                    'tokens not reported, cost unknown') + ('; allowance kept' if payment.stopping else '')
        if upper_bound:
            return 'estimated, at most ' + money(payment.cost)
        words = shared_cost_words(payment.cost if payment.usage else None, complete)
        return words if words == COST_NOT_KNOWN else 'estimated ' + words

    def _expire_payment(self, payment):
        # One turn can issue requests on both sides of midnight. There is no
        # guarantee that the earlier promotional rate applies to later ones.
        if payment.priced and not payment.reported and not is_priced(payment.model):
            payment.priced = False
            payment.upper_bound = False
            payment.priced_tokens = dict(payment.tokens or dict.fromkeys(COUNTERS, 0))
            self.ledger.record_openai_tokens(payment.reservation, payment.model,
                dict.fromkeys(COUNTERS, 0), unpriced=True, measured=False)

    @staticmethod
    def _unpriced_tokens(payment):
        if payment.tokens is None:
            return None
        tokens = {k: payment.tokens[k] - (payment.priced_tokens or {}).get(k, 0) for k in COUNTERS}
        return tokens if valid_counters(tokens) else None

    def _persist_baseline(self, tid, value):
        if tid in self._ephemeral_threads:
            return
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

    def api_billing_owned(self, record, grant=None):
        # Local ownership lookup only: safe on the UI thread. Reconnecting or
        # following another default cannot inherit a previous connection's answer.
        proof, _ = self.runtime._context(record)
        if grant is not None:
            model, tier, connection = grant
            if connection is not proof.connection or model != proof.model:
                raise ApiCreditsRefused('The API model or connection changed. Confirm its price before sending. Nothing was sent.')
            connection.authorize_unpriced(proof.native_id, model, tier)
        return proof.model, proof.connection

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
        self._has_custom_key = key_loader is not None
        self.ledger = ledger if ledger is not None else default_ledger('openai')
        super().__init__(connection, runtime=runtime)

    def capabilities(self, session):
        from dataclasses import replace
        # Side questions still use the subscription-only aside runner. Never
        # advertise that path for API chats until it has paid accounting.
        return replace(super().capabilities(session), aside=False, branch=False, rewind_files=False)

    def create_owned(self, cwd, title, *, model='gpt-5-nano', **kwargs):
        return super().create_owned(cwd, title, model=model, **kwargs)

    def collect(self):
        from dataclasses import replace
        from ..openai_billing import has_api_key
        from .base import Snapshot
        if not self._has_custom_key and not has_api_key():
            return Snapshot()
        snapshot = super().collect()
        snapshot.sessions = [replace(info, provider=self.provider_id, provider_label='Codex',
            key=f'{self.provider_id}:{info.cli_session_id}', api_credits=True) for info in snapshot.sessions]
        return snapshot

    def model_choices(self):
        from ..openai_prices import model_choices
        from .codex_models import read_models
        return model_choices(read_models(self.connection))

    def usage_lines(self, session):
        self._check(session)
        from ..openai_billing import usage_lines
        return usage_lines(self.ledger, session.key)

    def usage_warnings(self, session):
        self._check(session)
        return []


class OpenAiKeyTestRunner:
    """One tiny, cancellable test through the normal API backend; no wx."""
    def __init__(self, on_event, *, ledger=None, connection_factory=CodexApiConnection):
        self.on_event, self.ledger = on_event, ledger
        self.factory = connection_factory
        self._cancelled = threading.Event()
        self._connection = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self._cancelled.set()
        conn = self._connection
        if conn is not None:
            def close():
                try:
                    conn.close()
                except CodexRpcError:
                    pass
            threading.Thread(target=close, daemon=True).start()

    def _run(self):
        from dataclasses import replace
        from ..claude_cli import TurnEvent
        from .codex_owned import CodexOwnedThreads
        from .codex_execution import CodexTurnExecutor
        from ..codex_diagnostics import safe_reason
        done = threading.Event()
        result = []
        failures = []
        def observe(event):
            if event.kind == 'api_error':
                failures.append((event.data or {}).get('failure'))
            if event.kind == 'finished':
                result.append(event)
                done.set()
        try:
            conn = self.factory(ledger=self.ledger, on_event=observe, purpose='test')
            self._connection = conn
            if self._cancelled.is_set():
                return
            proof = CodexOwnedThreads(conn).start(str(platform_paths.codex_api_home()), 'Test OpenAI key',
                ephemeral=True, model='gpt-5-nano', effort='low', approval_policy='never')
            conn.accept_key_test(proof)
            runner = CodexTurnExecutor(conn)
            runner.start(proof, 'Do not use tools. Reply with exactly OK.')
            while not done.wait(.05):
                if self._cancelled.is_set():
                    return
                runner.drain()
            if not self._cancelled.is_set():
                event = result[-1]
                failed = bool(failures) or event.data.get('outcome') != 'completed'
                words = ('OpenAI refused your key. Replace it and test again.' if 'key_refused' in failures
                         else 'OpenAI reported a usage limit.' if 'usage_limit' in failures
                         else 'The OpenAI key test did not complete.' if failed else '')
                self.on_event(replace(event, is_error=failed, text=words))
        except Exception as error:
            if not self._cancelled.is_set():
                self.on_event(TurnEvent('failed', text=safe_reason(error) or 'Could not complete the OpenAI key test.', is_error=True))
        finally:
            if self._connection is not None:
                try:
                    self._connection.close()
                except CodexRpcError:
                    pass
