/**
 * Pipeline HUD Polling Logic
 */
const configEl = document.getElementById('pipeline-config');
const PIPELINE_ID = configEl.dataset.id;
let isTerminal = configEl.dataset.isTerminal === 'true';

document.addEventListener('DOMContentLoaded', () => {
    // Initialize progress bar width
    const progressFill = document.getElementById('progress-fill');
    if (progressFill && progressFill.dataset.progress) {
        progressFill.style.width = progressFill.dataset.progress + '%';
    }

    // Initial node sync from server-side logs
    const logs = JSON.parse(configEl.dataset.initialLogs || '[]');
    logs.forEach(log => {
        const node = document.getElementById('node-' + log.agent_name);
        const statusLabel = document.getElementById('status-' + log.agent_name);
        if (node) {
            node.classList.add(log.status === "SUCCESS" ? 'done' : (log.status === "FAILED" ? 'failed' : 'active'));
            statusLabel.innerText = log.status === "SUCCESS" ? 'Active Sequence Completed' : (log.status === "FAILED" ? 'Neural Process Interrupted' : 'Processing...');
        }
    });

    if (!isTerminal) poll();
});

function switchTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');
    btn.classList.add('active');
}

function toggleLog(header) {
    const body = header.nextElementSibling;
    const arrow = header.querySelector('svg');
    const isOpen = body.style.display === 'block';
    body.style.display = isOpen ? 'none' : 'block';
    arrow.style.transform = isOpen ? 'rotate(0deg)' : 'rotate(180deg)';
}

function copyOutput(btn) {
    const payload = document.getElementById('final-output-payload').innerText;
    navigator.clipboard.writeText(payload);
    const originalText = btn.innerText;
    btn.innerText = 'Copied!';
    setTimeout(() => btn.innerText = originalText, 2000);
}

async function poll() {
    if (isTerminal) return;

    try {
        const [statusRes, logsRes] = await Promise.all([
            fetch(`/api/pipeline/${PIPELINE_ID}/status/`),
            fetch(`/api/pipeline/${PIPELINE_ID}/logs/`)
        ]);
        const statusData = await statusRes.json();
        const logsData = await logsRes.json();

        // Update Header HUD
        const statusBadge = document.getElementById('status-badge');
        if (statusBadge) {
            statusBadge.className = 'badge badge-' + statusData.status;
            statusBadge.innerText = statusData.status;
        }

        const curIter = document.getElementById('cur-iter');
        if (curIter) curIter.innerText = statusData.iteration_count;

        const progressFill = document.getElementById('progress-fill');
        if (progressFill) {
            const pct = (statusData.iteration_count / statusData.max_iterations) * 100;
            progressFill.style.width = pct + '%';
        }

        // Update Agent Nodes
        const latestLogs = statusData.latest_logs || {};
        ['ORCHESTRATOR', 'PLANNER', 'REASONER', 'CRITIQUE', 'WRITER', 'EDITOR', 'REVIEWER'].forEach(name => {
            const node = document.getElementById('node-' + name);
            const statusLabel = document.getElementById('status-' + name);
            const log = latestLogs[name];

            if (log && node && statusLabel) {
                node.classList.remove('active', 'done', 'failed');
                if (log.status === 'SUCCESS') {
                    node.classList.add('done');
                    statusLabel.innerText = 'Active Sequence Completed';
                    statusLabel.style.color = 'var(--accent-emerald)';
                } else if (log.status === 'FAILED') {
                    node.classList.add('failed');
                    statusLabel.innerText = 'Neural Process Interrupted';
                    statusLabel.style.color = 'var(--danger)';
                } else {
                    node.classList.add('active');
                    statusLabel.innerText = 'Processing Stream...';
                    statusLabel.style.color = 'var(--text-dim)';
                }
            }
        });

        // Update Logs Stream
        const container = document.getElementById('logs-container');
        if (container && logsData.length > 0) {
            container.innerHTML = logsData.map(log => `
                <div class="log-entry">
                    <div class="log-entry-header" onclick="toggleLog(this)">
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <span class="badge badge-${log.status}">${log.status}</span>
                            <span style="font-weight: 700; color: #fff;">${log.agent_name}</span>
                            <span style="font-size: 0.75rem; color: var(--text-dim);">Iter ${log.iteration}</span>
                        </div>
                        <span style="color: var(--text-dim); transition: transform 0.2s;">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"></polyline></svg>
                        </span>
                    </div>
                    <div class="log-entry-body">
                        <div style="margin-bottom: 1rem;">
                            <div style="color: var(--accent); font-weight: 700; margin-bottom: 0.5rem; text-transform: uppercase; font-size: 0.65rem;">Neural Input</div>
                            <div style="color: var(--text-main); font-family: var(--font-mono);">${log.input_text.substring(0, 1000)}</div>
                        </div>
                        <div>
                            <div style="color: var(--accent-emerald); font-weight: 700; margin-bottom: 0.5rem; text-transform: uppercase; font-size: 0.65rem;">System Output</div>
                            <div style="color: var(--text-main); font-family: var(--font-mono);">${log.output_text.substring(0, 2000)}</div>
                        </div>
                    </div>
                </div>
            `).join('');
        }

        if (statusData.status === 'DONE') {
            isTerminal = true;
            const outRes = await fetch(`/api/pipeline/${PIPELINE_ID}/output/`);
            const outData = await outRes.json();
            document.getElementById('final-output-payload').innerText = outData.final_output;
            document.getElementById('live-indicator').innerHTML = 'Mission Accomplished';
            document.getElementById('live-indicator').style.color = 'var(--accent-emerald)';
        } else if (['FAILED', 'MAX_ITER'].includes(statusData.status)) {
            isTerminal = true;
            document.getElementById('live-indicator').innerHTML = 'Mission Terminated';
            document.getElementById('live-indicator').style.color = 'var(--danger)';
        }

    } catch (err) {
        console.error('HUD Sync Error:', err);
    }

    if (!isTerminal) setTimeout(poll, 3000);
}
