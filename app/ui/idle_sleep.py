import streamlit as st


def inject_idle_sleep(timeout_minutes: int = 10):
    """
    Injects the Idle Sleep monitor using the new V2 Component API (Streamlit 1.51+).
    This API allows JS to interact directly with the main document.
    """

    timeout_ms = int(timeout_minutes * 60 * 1000)

    # JavaScript using the st.components.v2 contract
    # Double braces {{ }} are used for f-string escaping
    js_code = f"""
    export default function(component) {{
        const targetWin = window.top;
        const targetDoc = targetWin.document;
        const TIMEOUT = {timeout_ms};
        const STORAGE_KEY = 'odis_last_activity';
        let idleTimer;

        console.log("Eco-Mode V2 (Bidi) Monitor Active.");

        function showSleepMode() {{
            if (targetDoc.getElementById('idle-sleep-overlay')) return;

            const overlay = targetDoc.createElement('div');
            overlay.id = 'idle-sleep-overlay';
            overlay.style.cssText = "position:fixed;top:0;left:0;width:100vw;height:100vh;background:#1B4429;z-index:9999999;display:flex;flex-direction:column;align-items:center;justify-content:center;color:white;font-family:sans-serif;text-align:center;padding:20px;backdrop-filter:blur(10px);";
            overlay.innerHTML = `
                <div style="background:rgba(0,0,0,0.2); padding:50px; border-radius:30px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 25px 50px -12px rgba(0,0,0,0.5);">
                    <h1 style="color:#FFD700; margin-bottom:24px; font-size:3rem; font-weight:800;">🌳 Mode Éco</h1>
                    <p style="margin-bottom:40px; font-size:1.3rem; max-width:450px; line-height:1.6;">
                        Session interrompue pour économiser des ressources.<br>
                        <b>L'instance Cloud Run est maintenant en veille.</b>
                    </p>
                    <button id="eco-resume-btn" style="background:#FFD700; color:#1B4429; border:none; padding:18px 50px; font-weight:800; border-radius:12px; cursor:pointer; font-size:1.2rem; box-shadow:0 4px 15px rgba(0,0,0,0.3);">REPRENDRE LA SESSION</button>
                </div>
            `;
            targetDoc.body.appendChild(overlay);
            targetDoc.getElementById('eco-resume-btn').onclick = () => targetWin.location.reload();

            // 1. Stop all current loading
            targetWin.stop();

            // 2. Kill all timers (Heartbeats, Fragments, etc.)
            const maxId = targetWin.setTimeout(() => {{}}, 0);
            for (let i = 0; i <= maxId; i++) {{
                targetWin.clearTimeout(i);
                targetWin.clearInterval(i);
            }}

            // 3. Sabotage outgoing network APIs to prevent further contact
            try {{
                targetWin.fetch = () => new Promise(() => {{}});
                targetWin.XMLHttpRequest.prototype.open = function() {{ 
                    console.log("Eco-Mode: Connection Blocked.");
                }};
                targetWin.XMLHttpRequest.prototype.send = function() {{}};
            }} catch (e) {{
                console.error("Eco-Mode: Error blocking network", e);
            }}

            console.log("Eco-Mode: Application connectivity severed.");
        }}

        function updateActivity() {{
            localStorage.setItem(STORAGE_KEY, Date.now());
        }}

        function check() {{
            const last = parseInt(localStorage.getItem(STORAGE_KEY) || Date.now());
            if (Date.now() - last > TIMEOUT) {{
                showSleepMode();
            }}
        }}

        // --- KEY FIX: Listen to the PARENT document for activity! ---
        ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart'].forEach(type => {{
            targetDoc.addEventListener(type, updateActivity, true);
        }});

        updateActivity();
        setInterval(check, 2000);
    }}
    """

    # 1. Declare the component
    # We set isolate_styles=False to ensure we can reach out of the component container easily.
    # Note: Streamlit 1.51.0 confirmed having st.components.v2.
    eco_comp = st.components.v2.component(
        "eco_mode_v2", js=js_code, isolate_styles=False
    )

    # 2. Mount the component
    eco_comp()
