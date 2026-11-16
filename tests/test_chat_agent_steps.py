"""test_chat_agent_steps.py - pins what `chat_agent._run_turn` does, step by step.

`_run_turn` is cut into named steps (canned replies, prompt assembly, the model
call, the final answer, one tool call). These tests drive the public entry
point only and assert what a caller can see: the returned dict, what was
persisted and in which order, what the model was sent, what the tools were
called with, what was logged. They passed against the single long function
before the cut and must keep passing after it.

No Postgres: history and persistence are in-memory, every collaborator that
would reach the network or the catalogue is a recorder.

Correr:  python3 -m unittest discover -s tests -p "test_chat_agent_steps.py"
"""

import contextlib
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.core import i18n, log_pii, theme  # noqa: E402
from app.modules.chat import chat_agent, chat_tools  # noqa: E402

CANAL = "web"
CLAVE = "test:steps"
ANON_TOOLS = list(chat_agent.ANONYMOUS_TOOLS)


def say(content):
    return {"role": "assistant", "content": content}


def call(name, args=None, call_id="c1", raw=None):
    return {"id": call_id, "type": "function", "function": {
        "name": name,
        "arguments": raw if raw is not None else json.dumps(args or {})}}


def ask(*tool_calls, content=None):
    return {"role": "assistant", "content": content, "tool_calls": list(tool_calls)}


class _Anon:
    """Stands in for the pii scrubber: tags what goes out, restores what comes back."""

    def __init__(self, usuario):
        self.usuario = usuario

    def scrub_messages(self, messages):
        return [dict(m, content=m["content"].replace("Delia", "<NOMBRE_1>"))
                if isinstance(m.get("content"), str) else m for m in messages]

    def restore(self, text):
        return text.replace("<NOMBRE_1>", "Delia")

    def restore_tool_call(self, tc):
        tc = copy.deepcopy(tc)
        fn = tc.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
            fn["arguments"] = fn["arguments"].replace("<NOMBRE_1>", "Delia")
        return tc


class StepsCase(unittest.TestCase):
    def run_turn(self, texto="hola", *, model=(), history=(), first="", used=1, key_ok=True,
                 breaker_ok=True, fake=None, usuario=None, names=None, impls=None,
                 confirm=None, urgent=False, scope=False, flags=(), routing_tool=None,
                 max_iter=None, max_calls=None, anon=None, evident="", requested="",
                 detected="", daily=50, route_text=None):
        """One real `_run_turn`; every collaborator is recorded in self.events."""
        self.events = []
        self.saved = []
        self.model_calls = []
        self.reports = []
        self.logs = []
        self.rec = {}
        script = list(model)
        history = [dict(m) for m in history]
        must_doctors, must_route, must_ground = (list(flags) + [False] * 3)[:3]

        def rec(name, ret):
            def fn(*a, **k):
                self.rec.setdefault(name, []).append((a, k))
                self.events.append(name)
                return ret(*a, **k) if callable(ret) else ret
            return fn

        def save(canal, clave, mensaje):
            self.saved.append((canal, clave, copy.deepcopy(mensaje)))
            self.events.append(("save", mensaje.get("role")))

        def model_fn(messages, tools, model_name, api_key, **kwargs):
            self.model_calls.append({"messages": copy.deepcopy(messages), "tools": tools,
                                     "model": model_name, "api_key": api_key, **kwargs})
            self.events.append("model")
            if not script:
                raise AssertionError("the model was called more often than scripted")
            nxt = script.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return {"choices": [{"message": nxt}]}

        env = {"TURNOS_FAKE_CHAT_AI": "0"}
        if fake is True:
            env = {"TURNOS_FAKE_CHAT_AI": "1"}
        elif fake == "fallback":
            env = {"TURNOS_FAKE_AI": "1"}
        elif fake == "chat_off":
            env = {"TURNOS_FAKE_CHAT_AI": "0", "TURNOS_FAKE_AI": "1"}

        def new_anon(u):
            self.rec.setdefault("for_account", []).append(u)
            return anon(u) if anon else _Anon(u)

        with contextlib.ExitStack() as st:
            p = lambda *a, **k: st.enter_context(patch.object(*a, **k))  # noqa: E731
            p(chat_agent, "_save", side_effect=save)
            p(chat_agent, "_load_history", side_effect=rec("load", lambda *a: copy.deepcopy(history)))
            p(chat_agent, "_first_user_message", side_effect=rec("first", first))
            p(chat_agent, "_turns_used", side_effect=rec("turns_used", used))
            p(chat_agent, "_max_turns_per_day", return_value=daily)
            p(chat_agent, "_memory_block", side_effect=rec("memory", "|MEM"))
            p(chat_agent, "_language_rule", side_effect=rec("lang_rule", "|LANG"))
            p(chat_agent, "_prompt_with_knowledge",
              side_effect=rec("knowledge", lambda *a: ("SYS", [{"titulo": "F1"}])))
            p(chat_agent, "_requested_language", side_effect=rec("requested", requested))
            p(chat_agent, "_evident_language", side_effect=rec("evident", evident))
            p(chat_agent.chat_language, "detect", side_effect=rec("detect", detected))
            p(chat_agent, "_model", return_value="m-x")
            p(chat_agent, "_log", side_effect=lambda m: self.logs.append(m))
            p(chat_agent, "_call_chat", side_effect=model_fn)
            p(chat_agent, "_env", side_effect=lambda k, d="": env.get(k, d))
            p(chat_agent, "_urgent_conversation", side_effect=rec("urgent", urgent))
            p(chat_agent, "_out_of_scope", side_effect=lambda t: scope)
            p(chat_agent, "emergency_line", side_effect=rec("emergency", lambda i: "EMERG(" + i + ")"))
            p(chat_agent, "_outage", side_effect=rec(
                "outage", lambda idioma, textos, urg: (
                    "OUT", [{"label": "Home", "href": "/"}], [("search_services", {"resultados": []})])))
            p(chat_agent, "_asks_who_attends", side_effect=lambda t: must_doctors)
            p(chat_agent, "_requires_catalog_routing", side_effect=lambda t: must_route)
            p(chat_agent, "_requires_slot_grounding", side_effect=lambda t: must_ground)
            p(chat_agent, "_routing_tool", side_effect=lambda tools: routing_tool)
            p(chat_agent, "_routing_response", side_effect=rec("routing_response", route_text or ""))
            p(chat_agent.pii, "for_account", side_effect=new_anon)
            p(chat_tools, "confirm_booking_reply", side_effect=rec("confirm", confirm))
            if names is not None:
                p(chat_agent, "_available_tools", return_value=list(names))
            if impls is not None:
                p(chat_tools, "TOOL_FNS", impls)
            if max_iter is not None:
                p(chat_agent, "MAX_ITER", max_iter)
            if max_calls is not None:
                p(chat_agent, "MAX_TOOL_CALLS_PER_TURN", max_calls)
            st.enter_context(patch("app.ai.ai_client.available", return_value=key_ok))
            st.enter_context(patch("app.ai.ai_client.primary_key", return_value="k"))
            st.enter_context(patch("app.ai.ai_health.available", return_value=breaker_ok))
            st.enter_context(patch(
                "app.ai.ai_health.report", side_effect=lambda e: self.reports.append(e)))
            out = chat_agent._run_turn(usuario, texto, CLAVE, CANAL)
        self.assertEqual(script, [], "the scripted model replies were not all used")
        return out

    def saved_roles(self):
        return [m["role"] for _, _, m in self.saved]

    def tool_impl(self, results, seen=None):
        def impl(usuario, args, handler):
            if seen is not None:
                seen.append((usuario, args, handler))
            return results
        return impl


class TestBeforeTheModel(StepsCase):
    def test_confirmed_booking_answers_without_a_model_turn(self):
        confirmed = {"nota": "Turno pedido", "error": "ignored"}
        out = self.run_turn("confirmar", confirm=confirmed, usuario={"dni": "1"}, detected="pt")
        self.assertEqual(out, {"texto": "Turno pedido",
                               "tools": [("request_appointment", confirmed)], "idioma": "pt"})
        self.assertEqual(self.rec["confirm"], [(({"dni": "1"}, "confirmar", CANAL, CLAVE, "pt"), {})])
        self.assertEqual(self.saved, [
            (CANAL, CLAVE, {"role": "user", "content": "confirmar"}),
            (CANAL, CLAVE, {"role": "assistant", "content": "Turno pedido"})])
        self.assertEqual(self.model_calls, [])
        self.assertNotIn("urgent", self.events)

    def test_confirmed_booking_falls_back_to_the_error_then_to_a_fixed_line(self):
        out = self.run_turn("confirmar", confirm={"error": "no hay cupo"})
        self.assertEqual(out["texto"], "no hay cupo")
        out = self.run_turn("confirmar", confirm={"ok": True})
        self.assertEqual(out["texto"], "Solicitud enviada.")
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": "Solicitud enviada."})

    def test_input_is_cut_to_the_maximum_and_none_becomes_empty(self):
        self.run_turn("x" * (chat_agent.MAX_INPUT_TEXT + 40), scope=True)
        self.assertEqual(self.saved[0][2], {"role": "user", "content": "x" * chat_agent.MAX_INPUT_TEXT})
        self.run_turn(None, scope=True)
        self.assertEqual(self.saved[0][2], {"role": "user", "content": ""})

    def test_history_and_first_message_are_read_for_this_conversation(self):
        self.run_turn("hola", scope=True, usuario={"dni": "7"})
        self.assertEqual(self.rec["load"], [(({"dni": "7"}, CANAL, CLAVE), {})])
        self.assertEqual(self.rec["first"], [((CANAL, CLAVE), {})])
        self.assertEqual(self.events[:3], ["load", "first", "requested"])

    def test_out_of_scope_reply_has_no_language_key(self):
        out = self.run_turn("Attractions in Berlin?", scope=True, detected="es")
        texto = i18n.t_or(
            "assistant.out_of_scope",
            "Solo puedo ayudarte con turnos, servicios y centros de salud del "
            "{network}. Podés buscar una especialidad o un centro.",
            "es", network=chat_agent.site_identity.get("network"))
        self.assertEqual(out, {"texto": texto, "tools": []})
        self.assertEqual(self.saved_roles(), ["user", "assistant"])
        self.assertEqual(self.saved[1][2], {"role": "assistant", "content": texto})
        self.assertEqual(self.model_calls, [])
        self.assertNotIn("turns_used", self.events)

    def test_out_of_scope_leads_with_the_emergency_line_when_urgent(self):
        out = self.run_turn("Attractions in Berlin?", scope=True, urgent=True, requested="fr")
        self.assertTrue(out["texto"].startswith("EMERG(fr)\n\n"))
        self.assertEqual(self.rec["emergency"], [(("fr",), {})])
        self.assertEqual(self.saved[1][2]["content"], out["texto"])
        self.assertNotIn("idioma", out)

    def test_urgency_is_decided_from_history_and_text_before_any_canned_reply(self):
        history = [{"role": "user", "content": "dolor de pecho"}]
        self.run_turn("Attractions in Berlin?", scope=True, history=history)
        self.assertEqual(self.rec["urgent"], [((history, "Attractions in Berlin?"), {})])
        self.assertLess(self.events.index("urgent"), self.events.index(("save", "assistant")))

    def test_daily_limit_blocks_only_beyond_the_cap_and_names_the_language(self):
        out = self.run_turn("hola", used=6, daily=5, detected="en")
        texto = i18n.t_or(
            "assistant.daily_limit",
            "Llegaste al límite de mensajes del asistente por hoy. Podés "
            "seguir desde las páginas de servicios y centros de {brand}.",
            "en", brand=theme.brand_name())
        self.assertEqual(out, {"texto": texto, "tools": [], "idioma": "en"})
        self.assertEqual(self.rec["turns_used"], [((CANAL, CLAVE), {})])
        self.assertEqual(self.saved[1][2], {"role": "assistant", "content": texto})
        self.assertEqual(self.model_calls, [])
        self.assertNotIn("knowledge", self.events)
        out = self.run_turn("hola", used=5, daily=5, model=[say("ok")])
        self.assertEqual(out["texto"], "ok")

    def test_daily_limit_leads_with_the_emergency_line_when_urgent(self):
        out = self.run_turn("hola", used=9, daily=5, urgent=True, requested="de")
        self.assertTrue(out["texto"].startswith("EMERG(de)\n\n"))
        self.assertEqual(out["idioma"], "de")
        self.assertEqual(self.saved[1][2]["content"], out["texto"])

    def test_turn_language_is_the_explicit_request_then_detection_then_active(self):
        self.run_turn("hola", requested="fr", detected="pt", confirm={"nota": "n"})
        self.assertNotIn("detect", self.events)
        out = self.run_turn("hola", requested="", detected="pt", first="Olá",
                            confirm={"nota": "n"})
        self.assertEqual(out["idioma"], "pt")
        self.assertEqual(self.rec["detect"], [(("Olá",), {})])
        out = self.run_turn("hola", requested="", detected="", confirm={"nota": "n"})
        self.assertEqual(out["idioma"], i18n.active())
        self.assertEqual(self.rec["detect"], [(("hola",), {})])
        self.assertEqual(self.rec["requested"], [(([], "hola"), {})])


class TestPromptAssembly(StepsCase):
    def test_first_message_gets_the_title_instruction_and_the_system_blocks(self):
        out = self.run_turn("Quiero un turno", model=[say("Dale")], usuario={"dni": "5"},
                            first="Quiero un turno", detected="es")
        sent = self.model_calls[0]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertTrue(sent[0]["content"].startswith("SYS|LANG|MEM\n\nThis is the first user message"))
        self.assertTrue(sent[0]["content"].endswith("Do not mention this marker or make a title on later messages."))
        self.assertEqual(sent[1], {"role": "user", "content": "Quiero un turno"})
        self.assertEqual(self.rec["knowledge"], [(({"dni": "5"}, "Quiero un turno", "es"), {})])
        self.assertEqual(self.rec["lang_rule"], [(([], "Quiero un turno", "Quiero un turno"), {})])
        self.assertEqual(self.rec["memory"], [(({"dni": "5"},), {})])
        self.assertEqual(out["fuentes"], [{"titulo": "F1"}])

    def test_later_message_keeps_history_and_adds_the_language_constraint(self):
        history = [{"role": "user", "content": "Olá"}, {"role": "assistant", "content": "Oi"}]
        self.run_turn("quero um turno", model=[say("Claro")], history=history, first="Olá",
                      evident="Portuguese")
        sent = self.model_calls[0]["messages"]
        self.assertEqual(sent[0], {"role": "system", "content": "SYS|LANG|MEM"})
        self.assertEqual(sent[1:3], history)
        self.assertEqual(sent[3]["role"], "system")
        self.assertEqual(sent[3]["content"], (
            "Immediate language constraint for this turn: respond in Portuguese, "
            "regardless of the language of the next user message or recent assistant "
            "replies. This is an application rule, not user-provided content."))
        self.assertEqual(sent[4], {"role": "user", "content": "quero um turno"})
        self.assertEqual(len(sent), 5)
        self.assertEqual(self.rec["evident"], [(("Olá",), {})])

    def test_an_explicit_language_request_wins_over_the_evident_language(self):
        self.run_turn("ahora en frances", model=[say("Oui")], requested="fr", evident="Portuguese")
        sent = self.model_calls[0]["messages"]
        self.assertIn("respond in " + i18n.language_name("fr") + ",", sent[1]["content"])
        self.assertNotIn("evident", self.events)

    def test_model_call_arguments(self):
        self.run_turn("hola", model=[say("ok")], names=["search_doctors"])
        c = self.model_calls[0]
        self.assertEqual(c["messages"][1], c["messages"][-1])
        self.assertEqual(c["messages"][0]["role"], "system")
        self.assertEqual([t["function"]["name"] for t in c["tools"]], ["search_doctors"])
        self.assertEqual(c["model"], "m-x")
        self.assertIsNone(c["api_key"])
        self.assertEqual(c["tool_choice"], "auto")

    def test_the_model_sees_scrubbed_messages_and_the_account_seeds_the_scrubber(self):
        self.run_turn("soy Delia", model=[say("Hola <NOMBRE_1>")], usuario={"dni": "9"})
        self.assertEqual(self.rec["for_account"], [{"dni": "9"}])
        self.assertEqual(self.model_calls[0]["messages"][-1],
                         {"role": "user", "content": "soy <NOMBRE_1>"})


class TestGates(StepsCase):
    def check_degraded(self, out, tools_before=()):
        self.assertEqual(out, {
            "texto": "OUT", "tools": list(tools_before) + [("search_services", {"resultados": []})],
            "fuentes": [{"titulo": "F1"}], "idioma": "es",
            "enlaces": [{"label": "Home", "href": "/"}]})
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": "OUT"})

    def test_no_key_degrades_before_any_model_call(self):
        history = [{"role": "user", "content": "primero"}, {"role": "assistant", "content": "r"},
                   {"role": "user", "content": "segundo"}]
        out = self.run_turn("tercero", key_ok=False, history=history, urgent=True, detected="es")
        self.check_degraded(out)
        self.assertEqual(self.rec["outage"], [(("es", ["tercero", "segundo", "primero"], True), {})])
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.saved_roles(), ["user", "assistant"])

    def test_open_breaker_degrades_before_any_model_call(self):
        out = self.run_turn("hola", breaker_ok=False, detected="es")
        self.check_degraded(out)
        self.assertEqual(self.rec["outage"], [(("es", ["hola"], False), {})])
        self.assertEqual(self.model_calls, [])

    def test_fake_model_skips_the_availability_gates(self):
        out = self.run_turn("hola", key_ok=False, breaker_ok=False, fake=True)
        self.assertEqual(out["texto"], "Recibido (fake): hola")
        self.assertEqual(self.model_calls, [])
        self.assertEqual(self.reports, [])
        self.assertNotIn("outage", self.events)

    def test_fake_switch_falls_back_to_the_general_one_unless_chat_turns_it_off(self):
        out = self.run_turn("hola", key_ok=False, fake="fallback")
        self.assertEqual(out["texto"], "Recibido (fake): hola")
        out = self.run_turn("hola", key_ok=False, fake="chat_off")
        self.assertEqual(out["texto"], "OUT")

    def test_fake_loop_runs_the_requested_tool_then_closes_with_text(self):
        seen = []
        out = self.run_turn('TOOL:search_doctors:{"query": "cardio"}', fake=True,
                            impls={"search_doctors": self.tool_impl({"total": 1}, seen)})
        self.assertEqual(out["texto"], "Listo (fake): ya usé la herramienta.")
        self.assertEqual(out["tools"], [("search_doctors", {"total": 1})])
        self.assertEqual(seen[0][1], {"query": "cardio"})
        self.assertEqual(self.saved_roles(), ["user", "assistant", "tool", "assistant"])

    def test_fake_marker_text_is_returned_as_the_model_wrote_it(self):
        out = self.run_turn("MARKERS:Elegí [opciones: a | b]", fake=True)
        self.assertEqual(out["texto"], "Elegí [opciones: a | b]")


class TestModelCall(StepsCase):
    def choices(self, flags, **kw):
        tool = call("search_services", {"query": "x"})
        self.run_turn("hola", model=[ask(tool), say("fin")], flags=flags,
                      routing_tool={"routing": True}, **kw)
        return [(c["tool_choice"], c["tools"]) for c in self.model_calls]

    def test_tool_choice_per_iteration_and_flags(self):
        impls = {"search_services": self.tool_impl({"resultados": []})}
        names = ANON_TOOLS
        pairs = self.choices((False, False, False), impls=impls)
        self.assertEqual([c for c, _ in pairs], ["auto", "auto"])
        self.assertEqual([[t["function"]["name"] for t in ts] for _, ts in pairs], [names, names])
        pairs = self.choices((True, False, False), impls=impls)
        self.assertEqual([c for c, _ in pairs], [
            {"type": "function", "function": {"name": "search_doctors"}}, "auto"])
        self.assertEqual([[t["function"]["name"] for t in ts] for _, ts in pairs], [names, names])
        pairs = self.choices((False, False, True), impls=impls)
        self.assertEqual([c for c, _ in pairs], ["required", "auto"])
        pairs = self.choices((False, True, False), impls=impls)
        self.assertEqual([c for c, _ in pairs], [
            {"type": "function", "function": {"name": "search_services"}}, "auto"])
        self.assertEqual(pairs[0][1], [{"routing": True}])
        self.assertEqual([t["function"]["name"] for t in pairs[1][1]], names)

    def test_who_attends_beats_routing_and_routing_beats_grounding(self):
        impls = {"search_services": self.tool_impl({"resultados": []})}
        pairs = self.choices((True, True, True), impls=impls)
        self.assertEqual(pairs[0][0], {"type": "function", "function": {"name": "search_doctors"}})
        self.assertNotEqual(pairs[0][1], [{"routing": True}])
        pairs = self.choices((False, True, True), impls=impls)
        self.assertEqual(pairs[0][0], {"type": "function", "function": {"name": "search_services"}})

    def test_the_routing_tool_is_only_used_when_there_is_one(self):
        self.run_turn("hola", model=[say("fin")], flags=(False, True, False), routing_tool=None)
        c = self.model_calls[0]
        self.assertEqual([t["function"]["name"] for t in c["tools"]], ANON_TOOLS)
        self.assertEqual(c["tool_choice"], {"type": "function", "function": {"name": "search_services"}})

    def test_a_good_call_reports_health_and_restores_the_real_data(self):
        seen = []
        out = self.run_turn(
            "soy Delia", usuario={"dni": "9"}, names=["search_doctors"],
            model=[ask(call("search_doctors", raw='{"q": "<NOMBRE_1>"}'), content="x"),
                   say("Hola <NOMBRE_1>")],
            impls={"search_doctors": self.tool_impl({"ok": 1}, seen)})
        self.assertEqual(self.reports, [None, None])
        self.assertEqual(out["texto"], "Hola Delia")
        self.assertEqual(seen[0][1], {"q": "Delia"})
        self.assertEqual(self.saved[1][2]["tool_calls"][0]["function"]["arguments"], '{"q": "Delia"}')

    def test_a_failed_model_call_reports_the_error_logs_it_and_degrades(self):
        err = TimeoutError("read timed out")
        out = self.run_turn("hola", model=[err], detected="es")
        self.assertEqual(out["texto"], "OUT")
        self.assertEqual(out["tools"], [("search_services", {"resultados": []})])
        self.assertEqual(self.reports, [err])
        self.assertEqual(self.logs, ["llamada al modelo falló: " + log_pii.exc(err)])
        self.assertEqual(self.rec["outage"], [(("es", ["hola"], False), {})])
        self.assertEqual(self.saved_roles(), ["user", "assistant"])
        self.assertEqual(self.saved[1][2]["content"], "OUT")

    def test_a_failure_after_a_tool_keeps_the_tool_results_in_the_degraded_reply(self):
        err = RuntimeError("boom")
        out = self.run_turn("hola", model=[ask(call("search_doctors")), err],
                            impls={"search_doctors": self.tool_impl({"total": 2})})
        self.assertEqual(out["tools"], [("search_doctors", {"total": 2}),
                                        ("search_services", {"resultados": []})])
        self.assertEqual(self.reports, [None, err])
        self.assertEqual(self.saved_roles(), ["user", "assistant", "tool", "assistant"])


class TestFinalAnswer(StepsCase):
    def test_empty_answer_asks_to_rephrase(self):
        out = self.run_turn("hola", model=[say("  \n ")], detected="es")
        self.assertEqual(out["texto"], i18n.t_or(
            "assistant.rephrase", "¿Podés repetirlo de otra forma?", "es"))
        out = self.run_turn("hola", model=[{"role": "assistant"}])
        self.assertEqual(out["texto"], "¿Podés repetirlo de otra forma?")

    def test_empty_answer_to_an_urgent_message_is_a_failed_turn(self):
        out = self.run_turn("hola", model=[say("")], urgent=True)
        self.assertEqual(out["texto"], "OUT")
        self.assertEqual(self.rec["outage"], [(("es", ["hola"], True), {})])
        self.assertEqual(self.reports, [None])
        out = self.run_turn("hola", model=[say(" respuesta ")], urgent=True)
        self.assertEqual(out["texto"], "respuesta")

    def test_the_answer_goes_through_five_passes_in_order(self):
        patches = [
            patch.object(chat_agent, "_unbacked_action_claim", side_effect=lambda r, t: (
                self.events.append(("claim", r, list(t))) or True)),
            patch.object(chat_agent, "_action_not_done", side_effect=lambda u, i: "NOT_DONE"),
            patch.object(chat_agent, "_strip_unsourced_coverage",
                         side_effect=lambda t, r, f, i: r + "|cov(" + t + "," + str(f) + "," + i + ")"),
            patch.object(chat_agent, "_localize_response_dates",
                         side_effect=lambda r, i: r + "|loc(" + i + ")"),
            patch.object(chat_agent, "_limit_jurisdiction", side_effect=lambda t, r: r + "|jur(" + t + ")"),
            patch.object(chat_agent, "_clean_site_path_mentions", side_effect=lambda r: r + "|clean"),
        ]
        with contextlib.ExitStack() as st:
            for pt in patches:
                st.enter_context(pt)
            out = self.run_turn("consulta", model=[say(" cruda ")], detected="es",
                                usuario={"dni": "4"})
        self.assertEqual(out, {
            "texto": "NOT_DONE|cov(consulta,[{'titulo': 'F1'}],es)|loc(es)|jur(consulta)|clean",
            "tools": [], "fuentes": [{"titulo": "F1"}], "idioma": "es"})
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": out["texto"]})
        self.assertIn(("claim", "cruda", []), self.events)

    def test_the_answer_is_kept_when_no_action_claim_is_unbacked(self):
        with patch.object(chat_agent, "_unbacked_action_claim", return_value=False), \
                patch.object(chat_agent, "_action_not_done", side_effect=AssertionError):
            out = self.run_turn("hola", model=[say("Todo bien")])
        self.assertEqual(out["texto"], "Todo bien")

    def test_an_unbacked_booking_claim_is_replaced_for_the_account(self):
        impls = {"request_appointment": self.tool_impl({"error": "denied"})}
        usuario = {"dni": "3"}
        out = self.run_turn(
            "pedime un turno", usuario=usuario, names=["request_appointment"], impls=impls,
            model=[ask(call("request_appointment")), say("Listo, tu turno quedó confirmado")],
            detected="es")
        self.assertEqual(out["texto"], chat_agent._action_not_done(usuario, "es"))
        self.assertEqual(out["tools"], [("request_appointment", {"error": "denied"})])


class TestToolCalls(StepsCase):
    def test_one_tool_call_is_persisted_run_and_fed_back_in_order(self):
        seen = []
        result = {"nombre": "Niño", "n": 1}
        usuario = {"dni": "8"}
        out = self.run_turn(
            "buscá", usuario=usuario, names=["search_doctors"],
            model=[ask(call("search_doctors", {"q": "ñ"}, call_id="abc"), content="pensando"),
                   say("Encontré uno")],
            impls={"search_doctors": self.tool_impl(result, seen)})
        self.assertEqual(out, {"texto": "Encontré uno", "tools": [("search_doctors", result)],
                               "fuentes": [{"titulo": "F1"}], "idioma": i18n.active()})
        asst = {"role": "assistant", "content": "pensando",
                "tool_calls": [call("search_doctors", {"q": "ñ"}, call_id="abc")]}
        tool = {"role": "tool", "tool_call_id": "abc", "name": "search_doctors",
                "content": json.dumps(result, ensure_ascii=False)}
        self.assertEqual([m for _, _, m in self.saved][1:], [asst, tool, say("Encontré uno")])
        self.assertIn("Niño", tool["content"])
        self.assertEqual(self.model_calls[1]["messages"][-2:], [asst, tool])
        u, args, handler = seen[0]
        self.assertIs(u, usuario)
        self.assertEqual(args, {"q": "ñ"})
        self.assertEqual(handler, chat_tools.ToolInvocation(
            channel=CANAL, conversation_key=CLAVE, language=i18n.active()))
        self.assertEqual(self.events.count("model"), 2)

    def test_only_the_first_four_calls_of_a_reply_run(self):
        seen = []
        calls = [call("search_doctors", {"i": i}, call_id=f"c{i}") for i in range(6)]
        out = self.run_turn("hola", model=[ask(*calls), say("fin")],
                            impls={"search_doctors": self.tool_impl({"ok": 1}, seen)})
        self.assertEqual([a for _, a, _ in seen], [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}])
        self.assertEqual(self.saved[1][2]["tool_calls"], calls[:4])
        self.assertEqual(len(out["tools"]), 4)

    def test_calls_past_the_per_turn_cap_get_an_error_and_do_not_count_as_results(self):
        seen = []
        model = [ask(call("search_doctors", call_id="a"), call("search_doctors", call_id="b")),
                 ask(call("search_doctors", call_id="c"), call("search_doctors", call_id="d")),
                 say("fin")]
        out = self.run_turn("hola", model=model, max_calls=3,
                            impls={"search_doctors": self.tool_impl({"ok": 1}, seen)})
        self.assertEqual(len(seen), 3)
        self.assertEqual(len(out["tools"]), 3)
        limited = [m for _, _, m in self.saved if m["role"] == "tool"][-1]
        self.assertEqual(limited, {
            "role": "tool", "tool_call_id": "d", "name": "",
            "content": json.dumps({"error": "límite de herramientas de este mensaje alcanzado"},
                                  ensure_ascii=False)})
        self.assertEqual(self.model_calls[2]["messages"][-1], limited)
        self.assertEqual(out["texto"], "fin")

    def test_a_missing_call_id_is_an_empty_string(self):
        tc = {"type": "function", "function": {"name": "search_doctors", "arguments": "{}"}}
        self.run_turn("hola", model=[ask(tc), say("fin")],
                      impls={"search_doctors": self.tool_impl({"ok": 1})})
        self.assertEqual(self.saved[2][2]["tool_call_id"], "")

    def test_bad_or_missing_arguments_become_an_empty_dict(self):
        seen = []
        model = [ask(call("search_doctors", raw="{not json", call_id="a"),
                      {"id": "b", "function": {"name": "search_doctors"}},
                      call("search_doctors", raw="", call_id="c")),
                 say("fin")]
        self.run_turn("hola", model=model, impls={"search_doctors": self.tool_impl({"ok": 1}, seen)})
        self.assertEqual([a for _, a, _ in seen], [{}, {}, {}])

    def test_a_tool_the_account_may_not_use_is_refused_without_running_it(self):
        seen = []
        out = self.run_turn(
            "hola", names=["search_doctors"],
            model=[ask(call("whoami"), {"id": "z"}), say("fin")],
            impls={"whoami": self.tool_impl({"x": 1}, seen), "search_doctors": self.tool_impl({})})
        unavailable = {"error": "esa herramienta no está disponible ahora — "
                                "si no tenés sesión iniciada, pedile a la "
                                "persona que entre en /account"}
        self.assertEqual(out["tools"], [("whoami", unavailable), ("", unavailable)])
        self.assertEqual(seen, [])
        self.assertEqual(self.saved[2][2]["name"], "whoami")
        self.assertEqual(self.saved[3][2]["name"], "")
        self.assertEqual(self.logs, [])

    def test_a_declared_tool_without_an_implementation_is_unknown(self):
        out = self.run_turn("hola", names=["ghost"], impls={},
                            model=[ask(call("ghost")), say("fin")])
        self.assertEqual(out["tools"], [("ghost", {"error": "tool desconocida"})])
        self.assertEqual(self.logs, [])

    def test_a_tool_that_raises_is_logged_and_answered_with_a_generic_error(self):
        boom = ValueError("db caída")

        def impl(usuario, args, handler):
            raise boom
        out = self.run_turn("hola", model=[ask(call("search_doctors")), say("fin")],
                            impls={"search_doctors": impl})
        self.assertEqual(out["tools"], [("search_doctors", {"error": "error interno ejecutando la herramienta"})])
        self.assertEqual(self.logs, ["tool search_doctors falló: " + log_pii.exc(boom)])
        self.assertEqual(out["texto"], "fin")

    def test_a_proposal_that_needs_confirmation_ends_the_turn(self):
        proposal = {"confirmation_required": True, "nota": "Confirmá tu turno", "href": "/c/1"}
        seen = []
        for name in ("request_appointment", "cancel_request"):
            out = self.run_turn(
                "pedime", usuario={"dni": "2"}, names=[name],
                model=[ask(call(name, call_id="a"), call(name, call_id="b"))],
                impls={name: self.tool_impl(proposal, seen)})
            self.assertEqual(out, {"texto": "Confirmá tu turno", "tools": [(name, proposal)],
                                   "fuentes": [{"titulo": "F1"}], "idioma": i18n.active(),
                                   "human_confirmation": "/c/1"})
            self.assertEqual(self.saved_roles(), ["user", "assistant", "tool", "assistant"])
            self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": "Confirmá tu turno"})
            self.assertEqual(self.events.count("model"), 1)
        self.assertEqual(len(seen), 2)

    def test_confirmation_flag_on_another_tool_does_not_end_the_turn(self):
        proposal = {"confirmation_required": True, "nota": "n", "href": "/h"}
        out = self.run_turn("hola", model=[ask(call("search_doctors")), say("fin")],
                            impls={"search_doctors": self.tool_impl(proposal)})
        self.assertEqual(out["texto"], "fin")
        self.assertNotIn("human_confirmation", out)

    def test_a_non_dict_result_is_stored_and_does_not_end_the_turn(self):
        out = self.run_turn("hola", model=[ask(call("search_doctors")), say("fin")],
                            impls={"search_doctors": self.tool_impl(["a", 1])})
        self.assertEqual(out["tools"], [("search_doctors", ["a", 1])])
        self.assertEqual(out["texto"], "fin")

    def test_routing_answer_ends_the_turn_on_the_first_search_services_with_results(self):
        result = {"resultados": [{"nombre": "Dermatología"}]}
        seen = []
        out = self.run_turn(
            "qué médico", flags=(False, True, False), route_text="RUTA", detected="en",
            model=[ask(call("search_services", call_id="a"), call("search_services", call_id="b"))],
            impls={"search_services": self.tool_impl(result, seen)})
        self.assertEqual(out, {"texto": "RUTA", "tools": [("search_services", result)],
                               "fuentes": [{"titulo": "F1"}], "idioma": "en"})
        self.assertEqual(self.rec["routing_response"], [((result, "en"), {"add_title": True})])
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": "RUTA"})
        self.assertEqual(self.saved_roles(), ["user", "assistant", "tool", "assistant"])

    def test_routing_answer_only_adds_a_title_to_the_first_message(self):
        result = {"resultados": [{"nombre": "X"}]}
        history = [{"role": "user", "content": "antes"}]
        self.run_turn("qué médico", flags=(False, True, False), route_text="RUTA", history=history,
                      model=[ask(call("search_services"))],
                      impls={"search_services": self.tool_impl(result)})
        self.assertEqual(self.rec["routing_response"][0][1], {"add_title": False})

    def test_routing_answer_is_skipped_when_it_does_not_apply(self):
        with_results = {"resultados": [{"nombre": "X"}]}
        cases = [
            ("no results", (False, True, False), "search_services", {"resultados": []}),
            ("other tool", (False, True, False), "search_doctors", with_results),
            ("not asked to route", (False, False, False), "search_services", with_results),
            ("not a dict", (False, True, False), "search_services", ["x"]),
        ]
        for label, flags, name, result in cases:
            with self.subTest(label):
                out = self.run_turn(
                    "hola", flags=flags, route_text="RUTA", routing_tool={"r": 1},
                    model=[ask(call(name)), say("fin")], names=[name],
                    impls={name: self.tool_impl(result)})
                self.assertEqual(out["texto"], "fin")
                self.assertNotIn("routing_response", self.events)

    def test_routing_answer_is_not_used_after_the_first_iteration(self):
        result = {"resultados": [{"nombre": "X"}]}
        out = self.run_turn(
            "hola", flags=(False, True, False), route_text="RUTA",
            model=[ask(call("search_doctors")), ask(call("search_services")), say("fin")],
            impls={"search_doctors": self.tool_impl({"a": 1}),
                   "search_services": self.tool_impl(result)})
        self.assertEqual(out["texto"], "fin")
        self.assertNotIn("routing_response", self.events)

    def test_an_empty_routing_answer_lets_the_model_continue(self):
        result = {"resultados": [{"nombre": "X"}]}
        out = self.run_turn(
            "hola", flags=(False, True, False), route_text="",
            model=[ask(call("search_services")), say("fin")],
            impls={"search_services": self.tool_impl(result)})
        self.assertEqual(out["texto"], "fin")
        self.assertEqual(len(self.rec["routing_response"]), 1)


class TestTooManySteps(StepsCase):
    def loop(self, **kw):
        return self.run_turn(
            "hola", max_iter=2,
            model=[ask(call("search_doctors", call_id="a")), ask(call("search_doctors", call_id="b"))],
            impls={"search_doctors": self.tool_impl({"ok": 1})}, **kw)

    def test_running_out_of_iterations_gives_the_fixed_apology(self):
        out = self.loop(detected="es")
        self.assertEqual(out, {
            "texto": i18n.t_or(
                "assistant.too_many_steps",
                "Se me complicó resolver esto en varios pasos — probá reformular el "
                "pedido, o seguí por la web.", "es"),
            "tools": [("search_doctors", {"ok": 1}), ("search_doctors", {"ok": 1})],
            "fuentes": [{"titulo": "F1"}], "idioma": "es"})
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": out["texto"]})
        self.assertEqual(self.events.count("model"), 2)

    def test_running_out_of_iterations_in_an_urgent_conversation_degrades(self):
        out = self.loop(urgent=True, detected="es")
        self.assertEqual(out["texto"], "OUT")
        self.assertEqual(out["tools"], [("search_doctors", {"ok": 1}), ("search_doctors", {"ok": 1}),
                                        ("search_services", {"resultados": []})])
        self.assertEqual(self.saved[-1][2], {"role": "assistant", "content": "OUT"})
        self.assertNotIn("complicó", " ".join(m.get("content") or "" for _, _, m in self.saved))


if __name__ == "__main__":
    unittest.main()
