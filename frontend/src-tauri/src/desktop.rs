use tauri::menu::{Menu, MenuItemBuilder, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Manager, Window, WindowEvent};

use crate::log_line;

pub(crate) const MAIN_WINDOW_LABEL: &str = "main";
const TRAY_ID: &str = "codex-cockpit-tray";
const OPEN_MENU_ID: &str = "open-codex-cockpit";
const QUIT_MENU_ID: &str = "quit-codex-cockpit";

#[cfg(target_os = "macos")]
const TRAY_ICON: tauri::image::Image<'_> = tauri::include_image!("./icons/tray-template.png");

#[cfg(target_os = "windows")]
const TRAY_ICON: tauri::image::Image<'_> = tauri::include_image!("./icons/32x32.png");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DesktopAction {
    ShowMainWindow,
    Quit,
}

fn menu_action(menu_id: &str) -> Option<DesktopAction> {
    match menu_id {
        OPEN_MENU_ID => Some(DesktopAction::ShowMainWindow),
        QUIT_MENU_ID => Some(DesktopAction::Quit),
        _ => None,
    }
}

fn tray_action(button: MouseButton, button_state: MouseButtonState) -> Option<DesktopAction> {
    (button == MouseButton::Left && button_state == MouseButtonState::Up)
        .then_some(DesktopAction::ShowMainWindow)
}

fn should_hide_on_close(window_label: &str) -> bool {
    window_label == MAIN_WINDOW_LABEL
}

fn log_window_error(action: &str, error: tauri::Error) {
    log_line(&format!("[cockpit] Failed to {action}: {error}"));
}

pub(crate) fn show_main_window(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    if let Err(error) = app.show() {
        log_window_error("show application", error);
    }

    let Some(window) = app.get_webview_window(MAIN_WINDOW_LABEL) else {
        log_line("[cockpit] Main window is unavailable");
        return;
    };

    if let Err(error) = window.show() {
        log_window_error("show main window", error);
    }
    if let Err(error) = window.unminimize() {
        log_window_error("unminimize main window", error);
    }
    if let Err(error) = window.set_focus() {
        log_window_error("focus main window", error);
    }
}

pub(crate) fn handle_window_event(window: &Window, event: &WindowEvent) {
    if !should_hide_on_close(window.label()) {
        return;
    }

    if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        if let Err(error) = window.hide() {
            log_window_error("hide main window", error);
        }
    }
}

pub(crate) fn begin_exit(app: &AppHandle) {
    drop(app.remove_tray_by_id(TRAY_ID));
    for window in app.webview_windows().into_values() {
        if let Err(error) = window.hide() {
            log_window_error("hide window during exit", error);
        }
    }
}

pub(crate) fn setup(app: &mut App) -> tauri::Result<()> {
    let open = MenuItemBuilder::with_id(OPEN_MENU_ID, "打开 Codex Cockpit Lite").build(app)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItemBuilder::with_id(QUIT_MENU_ID, "退出 Codex Cockpit Lite")
        .accelerator("CmdOrCtrl+Q")
        .build(app)?;
    let menu = Menu::with_items(app, &[&open, &separator, &quit])?;

    let tray = TrayIconBuilder::with_id(TRAY_ID)
        .icon(TRAY_ICON)
        .tooltip("Codex Cockpit Lite")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match menu_action(event.id().as_ref()) {
            Some(DesktopAction::ShowMainWindow) => show_main_window(app),
            Some(DesktopAction::Quit) => app.exit(0),
            None => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button,
                button_state,
                ..
            } = event
            {
                if tray_action(button, button_state) == Some(DesktopAction::ShowMainWindow) {
                    show_main_window(tray.app_handle());
                }
            }
        });

    #[cfg(target_os = "macos")]
    let tray = tray.icon_as_template(true);

    tray.build(app)?;

    #[cfg(target_os = "macos")]
    app.handle()
        .set_activation_policy(tauri::ActivationPolicy::Accessory)?;

    show_main_window(app.handle());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        menu_action, should_hide_on_close, tray_action, DesktopAction, MAIN_WINDOW_LABEL,
        OPEN_MENU_ID, QUIT_MENU_ID,
    };
    use tauri::tray::{MouseButton, MouseButtonState};

    #[test]
    fn completed_left_click_opens_main_window() {
        assert_eq!(
            tray_action(MouseButton::Left, MouseButtonState::Up),
            Some(DesktopAction::ShowMainWindow)
        );
        assert_eq!(tray_action(MouseButton::Left, MouseButtonState::Down), None);
        assert_eq!(tray_action(MouseButton::Right, MouseButtonState::Up), None);
    }

    #[test]
    fn menu_ids_map_to_desktop_actions() {
        assert_eq!(
            menu_action(OPEN_MENU_ID),
            Some(DesktopAction::ShowMainWindow)
        );
        assert_eq!(menu_action(QUIT_MENU_ID), Some(DesktopAction::Quit));
        assert_eq!(menu_action("unknown"), None);
    }

    #[test]
    fn only_main_window_closes_to_tray() {
        assert!(should_hide_on_close(MAIN_WINDOW_LABEL));
        assert!(!should_hide_on_close("secondary"));
    }
}
