import streamlit as st

def inject_idle_sleep(timeout_minutes: int = 10):
    """
    Injects a robust, invisible Idle Sleep monitor.
    Uses st.components.v1.html (height=0) to monitor activity and
    injects a full-screen overlay into the parent window when idle.
    """
    
    timeout_ms = int(timeout_minutes * 60 * 1000)
    
    # We use st.components.v1.html but with height=0 to be invisible.
    # The JS will detect idle and then modify the parent DOM for the overlay.
    js_payload = f"""
    <script>
    (function() {{
        const TIMEOUT = {timeout_ms};
        const STORAGE_KEY = 'odis_last_activity';
        const target = window.parent;

        function updateActivity() {{
            localStorage.setItem(STORAGE_KEY, Date.now());
        }}

        // Listen for activity in both this frame and parent
        ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart'].forEach(type => {{
            document.addEventListener(type, updateActivity, true);
            try {{ target.document.addEventListener(type, updateActivity, true); }} catch(e) {{}}
        }});

        function triggerSleep() {{
            console.log("Eco-Mode: Idle timeout reached. Pausing session.");
            
            // 1. Terminate all traffic in parent
            try {{
                target.window.stop();
                let id = target.setInterval(function() {{}}, 0);
                while (id--) target.clearInterval(id);
            }} catch(e) {{}}

            // 2. Clear future networking (monkeypatch)
            try {{
                target.fetch = function() {{ return new Promise(() => {{}}); }};
                target.XMLHttpRequest.prototype.open = function() {{ }};
            }} catch(e) {{}}

            // 3. Create and show overlay in the parent window
            try {{
                if (target.document.getElementById('idle-sleep-overlay')) return;

                const overlay = target.document.createElement('div');
                overlay.id = 'idle-sleep-overlay';
                overlay.style.cssText = "position:fixed;top:0;left:0;width:100vw;height:100vh;background:#1B4429;z-index:9999999;display:flex;flex-direction:column;align-items:center;justify-content:center;color:white;font-family:sans-serif;text-align:center;padding:20px;";
                overlay.innerHTML = `
                    <div style="background:rgba(0,0,0,0.2); padding:50px; border-radius:30px; border:1px solid rgba(255,255,255,0.1); box-shadow:0 25px 50px -12px rgba(0,0,0,0.5);">
                        <h1 style="color:#FFD700; margin-bottom:24px; font-size:3rem; font-weight:800;">🌳 Mode Éco</h1>
                        <p style="margin-bottom:40px; font-size:1.3rem; max-width:450px; line-height:1.6;">
                            Session interrompue pour économiser des ressources.<br>
                        </p>
                        <button id="eco-resume-btn" style="background:#FFD700; color:#1B4429; border:none; padding:18px 50px; font-weight:800; border-radius:12px; cursor:pointer; font-size:1.2rem; box-shadow:0 4px 15px rgba(0,0,0,0.3);">REPRENDRE</button>
                    </div>
                `;
                target.document.body.appendChild(overlay);
                target.document.getElementById('eco-resume-btn').onclick = () => target.location.reload();
            }} catch(e) {{
                // Robust fallback
                target.alert("🌳 Mode Éco\\n\\nSession mise en veille due à l'inactivité.\\n\\nCliquez sur OK pour reprendre.");
                target.location.reload();
            }}
        }}

        function check() {{
            const last = parseInt(localStorage.getItem(STORAGE_KEY) || Date.now());
            if (Date.now() - last > TIMEOUT) {{
                triggerSleep();
            }}
        }}

        setInterval(check, 2000);
        if (!localStorage.getItem(STORAGE_KEY)) updateActivity();
        console.log("Eco-Mode Monitor (v1 Silent) Active.");
    }})();
    </script>
    """
    
    st.components.v1.html(js_payload, height=0)
