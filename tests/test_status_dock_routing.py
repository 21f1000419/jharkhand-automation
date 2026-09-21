from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from core.models import UiEvent
from ui.automation_status import AutomationStatusWindow
from ui.main_window import MainWindow
from ui.run_tab import AutomationTab


class StatusDockRoutingTests(unittest.TestCase):
    def window_with_tabs(self) -> tuple[MainWindow, MagicMock, MagicMock]:
        window = MainWindow.__new__(MainWindow)
        first = MagicMock()
        first.tab_id = 1
        first.run_id = "1"
        first.display_name = "ID 1"
        first.browser_recovery_pending = False
        first.browser_recovery_ready = False
        second = MagicMock()
        second.tab_id = 2
        second.run_id = "2"
        second.display_name = "ID 2"
        second.browser_recovery_pending = False
        second.browser_recovery_ready = False
        window.tabs = {1: first, 2: second}
        window._record_ui_action = MagicMock()  # type: ignore[method-assign]
        window._update_summary = MagicMock()  # type: ignore[method-assign]
        window.controller = MagicMock()
        return window, first, second

    def test_dock_controls_target_only_the_selected_id(self) -> None:
        window, first, second = self.window_with_tabs()

        window._dock_pause("2")
        window._dock_resume("2")
        window._dock_stop("2")
        window._dock_error_decision("2", "retry")

        first.pause.assert_not_called()
        first.resume.assert_not_called()
        first.decide_error.assert_not_called()
        second.pause.assert_called_once_with()
        second.resume.assert_called_once_with()
        # Dock Stop/Error target one browser via the controller so other
        # browsers of the ID can continue.
        window.controller.stop.assert_called_once_with("2")
        window.controller.decide_error.assert_called_once_with("2", "retry")
        second.stop.assert_not_called()
        second.decide_error.assert_not_called()

    def test_browser_recovery_controls_target_only_the_closed_id(self) -> None:
        window, first, second = self.window_with_tabs()
        second.browser_recovery_pending = True

        window._dock_browser_recovery("2", "retry")
        window._dock_stop("2")

        first.recover_browser.assert_not_called()
        first.dismiss_browser_recovery.assert_not_called()
        second.recover_browser.assert_called_once_with("retry")
        second.dismiss_browser_recovery.assert_called_once_with()
        second.stop.assert_not_called()

    def test_start_all_starts_every_enabled_id_without_individual_clicks(self) -> None:
        window, first, second = self.window_with_tabs()
        window.run_status_var = MagicMock()
        first.is_enabled = True
        second.is_enabled = True
        first.start.return_value = True
        second.start.return_value = True

        window._start_all()

        first.start.assert_called_once_with(show_errors=False)
        second.start.assert_called_once_with(show_errors=False)
        window.run_status_var.set.assert_called_once_with("Started 2 ID(s); skipped 0 disabled")

    def test_start_all_restarts_complete_ids_for_recheck(self) -> None:
        window, first, second = self.window_with_tabs()
        window.run_status_var = MagicMock()
        window.tab_states = {1: "Complete", 2: "Complete"}
        first.is_enabled = True
        second.is_enabled = True
        first.start.return_value = True
        second.start.return_value = True

        window._start_all()

        first.start.assert_called_once_with(show_errors=False)
        second.start.assert_called_once_with(show_errors=False)
        window.run_status_var.set.assert_called_once_with("Started 2 ID(s); skipped 0 disabled")

    def test_recovery_is_only_offered_while_another_run_is_active(self) -> None:
        window, first, second = self.window_with_tabs()
        first.is_active = True
        first.portal_session_open = True
        second.is_active = True
        second.portal_session_open = True

        self.assertTrue(window.should_offer_browser_recovery(first))

        second.is_active = False
        second.portal_session_open = False
        self.assertFalse(window.should_offer_browser_recovery(first))

        second.browser_recovery_pending = True
        self.assertTrue(window.should_offer_browser_recovery(first))

    def test_closed_browser_waits_for_cleanup_then_offers_recovery(self) -> None:
        owner = MagicMock()
        owner.should_offer_browser_recovery.return_value = True
        tab = AutomationTab.__new__(AutomationTab)
        tab.owner = owner
        tab.config = MagicMock(display_name="ID 1")
        tab.running = True
        tab.starting = False
        tab.paused = False
        tab.auto_waiting = False
        tab.portal_session_open = True
        tab.browser_recovery_pending = False
        tab.browser_recovery_ready = False
        tab.current_row_number = 4
        tab.current_unit_number = 2
        tab._set_buttons = MagicMock()  # type: ignore[method-assign]
        tab.set_state = MagicMock()  # type: ignore[method-assign]

        tab.handle_event(UiEvent("browser_closed", "Chrome closed"))

        self.assertTrue(tab.browser_recovery_pending)
        self.assertFalse(tab.browser_recovery_ready)
        tab.set_state.assert_called_with(
            "Browser closed",
            "This browser was closed. Finishing cleanup before it can restart...",
            dock_id=None,
        )
        owner.show_browser_recovery.assert_called_with(
            tab,
            "This browser was closed. Finishing cleanup before it can restart...",
            ready=False,
            dock_id=None,
        )

        tab.handle_event(UiEvent("run_stopped", "Current row remains retryable"))
        self.assertTrue(tab.browser_recovery_pending)

        tab.handle_event(UiEvent("session_finished", "Ready"))
        self.assertTrue(tab.browser_recovery_ready)
        owner.show_browser_recovery.assert_called_with(
            tab,
            "Retry this row, skip it and open the next row, or stop this ID.",
            ready=True,
            dock_id=None,
        )

    def test_start_button_waits_for_browser_cleanup(self) -> None:
        tab = AutomationTab.__new__(AutomationTab)
        tab.config = MagicMock(enabled=True)
        tab.running = False
        tab.starting = False
        tab.browser_recovery_pending = True
        tab.browser_recovery_ready = False
        tab.start_button = MagicMock()
        tab.pause_button = MagicMock()
        tab.resume_button = MagicMock()
        tab.stop_button = MagicMock()
        tab.toggle_enabled_button = MagicMock()
        tab.delete_profile_button = MagicMock()
        tab.portal_session_open = False

        tab._set_buttons()

        tab.start_button.configure.assert_called_once_with(state="disabled")

        tab.browser_recovery_ready = True
        tab.start_button.reset_mock()
        tab._set_buttons()

        tab.start_button.configure.assert_called_once_with(state="normal")

    def test_closed_browser_has_no_recovery_card_for_single_run(self) -> None:
        owner = MagicMock()
        owner.should_offer_browser_recovery.return_value = False
        tab = AutomationTab.__new__(AutomationTab)
        tab.owner = owner
        tab.config = MagicMock(display_name="ID 1")
        tab.running = True
        tab.starting = False
        tab.paused = False
        tab.auto_waiting = False
        tab.portal_session_open = True
        tab.browser_recovery_pending = False
        tab.browser_recovery_ready = False
        tab._set_buttons = MagicMock()  # type: ignore[method-assign]
        tab.set_state = MagicMock()  # type: ignore[method-assign]

        tab.handle_event(UiEvent("browser_closed", "Chrome closed"))

        self.assertFalse(tab.browser_recovery_pending)
        tab.set_state.assert_called_with("Stopped", "Chrome closed", dock_id=None)
        owner.show_browser_recovery.assert_not_called()

    def test_workflow_only_close_signal_keeps_multi_run_card(self) -> None:
        owner = MagicMock()
        owner.should_offer_browser_recovery.return_value = True
        tab = AutomationTab.__new__(AutomationTab)
        tab.owner = owner
        tab.config = MagicMock(display_name="ID 2")
        tab.running = True
        tab.starting = False
        tab.paused = False
        tab.auto_waiting = False
        tab.portal_session_open = True
        tab.browser_recovery_pending = False
        tab.browser_recovery_ready = False
        tab._set_buttons = MagicMock()  # type: ignore[method-assign]
        tab.set_state = MagicMock()  # type: ignore[method-assign]

        tab.handle_event(
            UiEvent(
                "run_stopped",
                "A browser was closed. The current row remains retryable.",
                {"browser_closed": True},
            )
        )

        self.assertTrue(tab.browser_recovery_pending)
        tab.set_state.assert_called_with(
            "Browser closed",
            "This browser was closed. Finishing cleanup before it can restart...",
            dock_id=None,
        )
        owner.show_browser_recovery.assert_called_once_with(
            tab,
            "This browser was closed. Finishing cleanup before it can restart...",
            ready=False,
            dock_id=None,
        )

    def test_payment_events_release_topmost_for_only_the_payment_id(self) -> None:
        window, first, _second = self.window_with_tabs()
        dock = MagicMock()
        dock.exists = True
        window.automation_status_window = dock

        window._handle_event(
            UiEvent("payment_state", "Payment slot granted.", {"state": "slot_granted"}, "1")
        )
        dock.set_payment_active.assert_called_once_with("1", True)

        dock.reset_mock()
        window._handle_event(
            UiEvent("payment_state", "Payment slot released.", {"state": "slot_released"}, "1")
        )
        dock.set_payment_active.assert_called_once_with("1", False)
        self.assertEqual(first.handle_event.call_count, 2)

    def test_status_dock_stays_topmost_during_payment(self) -> None:
        dock = AutomationStatusWindow.__new__(AutomationStatusWindow)
        dock.window = MagicMock()

        dock.set_payment_active("1", True)

        dock.window.attributes.assert_called_once_with("-topmost", True)
        dock.window.lift.assert_called_once_with()

    def test_macos_dock_uses_a_floating_utility_window(self) -> None:
        dock = AutomationStatusWindow.__new__(AutomationStatusWindow)
        dock.window = MagicMock()
        dock.window._w = ".automation_status"
        dock._macos_floating_style_applied = False

        with patch("ui.automation_status.sys.platform", "darwin"):
            dock._configure_macos_floating_style()

        dock.window.tk.call.assert_called_once_with(
            "::tk::unsupported::MacWindowStyle",
            "style",
            ".automation_status",
            "utility",
        )

    def test_starting_state_creates_one_colored_card_for_the_id(self) -> None:
        window, first, _second = self.window_with_tabs()
        dock = MagicMock()
        dock.exists = True
        window.automation_status_window = dock
        window.tab_states = {}
        window.notebook = MagicMock()
        window._tab_accent_image = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
        first.frame = MagicMock()
        first.is_enabled = True
        first.running = False
        first.starting = True
        first.paused = False
        first.auto_waiting = False
        first.portal_session_open = False

        window.update_tab_state(first, "Starting", "Opening Chrome")

        dock.begin_run.assert_called_once_with("1", "ID 1", "#2563eb", "Opening Chrome")
        dock.set_controls.assert_called_once_with(
            "1",
            running=False,
            starting=True,
            paused=False,
            auto_waiting=False,
            portal_open=False,
        )

    def test_starting_state_creates_one_panel_per_browser_with_concise_names(self) -> None:
        window, first, _second = self.window_with_tabs()
        dock = MagicMock()
        dock.exists = True
        dock.has_run.return_value = False
        window.automation_status_window = dock
        window.tab_states = {}
        window.notebook = MagicMock()
        window._tab_accent_image = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
        first.frame = MagicMock()
        first.run_id = "3"
        first.tab_id = 3
        first.display_name = "ID 3"
        first.config = MagicMock(browser_count=3)
        first.is_enabled = True
        first.running = False
        first.starting = True
        first.paused = False
        first.auto_waiting = False
        first.portal_session_open = False
        window.tabs = {3: first}

        window.update_tab_state(first, "Starting", "Opening 3 browsers")

        begun = {call.args[0] for call in dock.begin_run.call_args_list}
        self.assertEqual(begun, {"3.1", "3.2", "3.3"})
        titles = {call.args[1] for call in dock.begin_run.call_args_list}
        self.assertEqual(titles, {"ID 3 | B3.1", "ID 3 | B3.2", "ID 3 | B3.3"})

    def test_parallel_browser_status_updates_only_its_own_panel(self) -> None:
        window, first, _second = self.window_with_tabs()
        dock = MagicMock()
        dock.exists = True
        dock.has_run.side_effect = lambda dock_id: dock_id in {"3.1", "3.2", "3.3"}
        window.automation_status_window = dock
        window.tab_states = {}
        window.notebook = MagicMock()
        window._tab_accent_image = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
        first.frame = MagicMock()
        first.run_id = "3"
        first.tab_id = 3
        first.display_name = "ID 3"
        first.config = MagicMock(browser_count=3)
        first.is_enabled = True
        first.running = True
        first.starting = False
        first.paused = False
        first.auto_waiting = False
        first.portal_session_open = True
        window.tabs = {3: first}

        window.update_tab_state(first, "Running", "Filling form", dock_id="3.2")

        dock.set_status.assert_called_once_with("3.2", "Running", "Filling form")
        dock.set_controls.assert_called_once_with(
            "3.2",
            running=True,
            starting=False,
            paused=False,
            auto_waiting=False,
            portal_open=True,
        )

    def test_dock_focus_targets_the_specific_parallel_browser(self) -> None:
        window, first, _second = self.window_with_tabs()
        window.controller = MagicMock()
        window.controller.get_portal_window_handle.return_value = 4242
        window.run_status_var = MagicMock()

        with patch("automation.browser._restore_and_activate_window", return_value=True):
            window._dock_focus_browser("3.2")

        window.controller.get_portal_window_handle.assert_called_once_with("3.2")

    def test_dock_worker_id_resolves_to_its_base_id_tab(self) -> None:
        window, first, _second = self.window_with_tabs()
        first.tab_id = 3
        window.tabs = {3: first}
        self.assertIs(window._dock_tab("3.2"), first)
        self.assertIs(window._dock_tab("3"), first)

    def test_dock_stop_targets_only_that_browser(self) -> None:
        window, first, _second = self.window_with_tabs()
        first.tab_id = 3
        first.run_id = "3"
        first.browser_recovery_pending = False
        window.tabs = {3: first}
        window._dock_stop("3.2")
        window.controller.stop.assert_called_once_with("3.2")
        first.stop.assert_not_called()

    def test_worker_stopped_removes_only_its_own_panel(self) -> None:
        window, first, _second = self.window_with_tabs()
        dock = MagicMock()
        dock.exists = True
        dock.has_run.side_effect = lambda dock_id: dock_id in {"3.1", "3.2"}
        window.automation_status_window = dock
        window.tabs = {3: first}
        first.tab_id = 3
        first.run_id = "3"
        first.handle_event = MagicMock()  # type: ignore[method-assign]
        window._update_summary = MagicMock()  # type: ignore[method-assign]

        window._handle_event(
            UiEvent("worker_stopped", "B3.2 stopped.", {"dock_id": "3.2"}, "3")
        )

        dock.remove_run.assert_called_once_with("3.2")
        first.handle_event.assert_called_once()


class MainMenuTests(unittest.TestCase):
    @patch("ui.main_window.filedialog.askopenfilename", return_value="")
    def test_import_config_uses_cross_platform_file_patterns(
        self, askopenfilename: MagicMock
    ) -> None:
        window = MainWindow.__new__(MainWindow)
        window.root = MagicMock()
        window.tabs = {}
        window._record_ui_action = MagicMock()  # type: ignore[method-assign]

        window._import_configs()

        filetypes = askopenfilename.call_args.kwargs["filetypes"]
        self.assertEqual(filetypes[0][1], ("*.estampcfg", "*.ecfg"))
        self.assertNotIn(";", repr(filetypes))

    @patch("ui.main_window.managed_firefox_is_installed", return_value=False)
    @patch("ui.main_window.tk.Menu")
    def test_top_level_menu_contains_only_macos_compatible_cascades(
        self, menu_class: MagicMock, _managed_firefox_is_installed: MagicMock
    ) -> None:
        top_menu = MagicMock()
        downloads_menu = MagicMock()
        config_menu = MagicMock()
        profile_menu = MagicMock()
        activity_menu = MagicMock()
        menu_class.side_effect = [
            top_menu,
            downloads_menu,
            config_menu,
            profile_menu,
            activity_menu,
        ]
        window = MainWindow.__new__(MainWindow)
        window.root = MagicMock()
        window.managed_firefox_downloading = False

        window._build_menu()

        top_menu.add_command.assert_not_called()
        self.assertEqual(
            [call.kwargs["label"] for call in top_menu.add_cascade.call_args_list],
            ["Downloads", "IDs & Settings", "OCR profile", "Activity"],
        )
        self.assertEqual(
            [call.kwargs["label"] for call in downloads_menu.add_command.call_args_list],
            [
                "Download managed Firefox",
                "Download CSV format...",
                "Download Undownloaded Certificates...",
            ],
        )
        downloads_menu.entryconfigure.assert_called_once_with(
            0, label="Download managed Firefox", state="normal"
        )
        window.root.configure.assert_called_once_with(menu=top_menu)


if __name__ == "__main__":
    unittest.main()
