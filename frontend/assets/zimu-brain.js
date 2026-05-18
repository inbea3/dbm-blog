/**
 * 小zimu — 博客第二大脑（RAG + 多轮对话）
 */
(function () {
    const STORAGE_KEY = 'zimu-brain-history-v1';
    const MAX_STORED = 24;

    function esc(s) {
        return String(s)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;');
    }

    function apiRoot() {
        return `${window.location.origin}/api`;
    }

    function getPageContext() {
        if (window.__ZIMU_PAGE__) return window.__ZIMU_PAGE__;
        if (/detail\.html$/i.test(location.pathname)) {
            const id = new URLSearchParams(location.search).get('id');
            const titleEl = document.getElementById('article-title');
            if (id) {
                return {
                    type: 'article',
                    id: String(id),
                    title: titleEl ? titleEl.textContent.trim() : '',
                };
            }
        }
        return null;
    }

    function loadHistory() {
        try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            const data = raw ? JSON.parse(raw) : [];
            return Array.isArray(data) ? data : [];
        } catch {
            return [];
        }
    }

    function saveHistory(messages) {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-MAX_STORED)));
        } catch {
            /* ignore */
        }
    }

    function buildUi() {
        if (document.getElementById('zimu-brain-launcher')) return;

        const launcher = document.createElement('button');
        launcher.id = 'zimu-brain-launcher';
        launcher.type = 'button';
        launcher.setAttribute('aria-label', '打开小zimu');
        launcher.innerHTML =
            '<span class="zb-icon"><i class="fas fa-brain"></i></span><span>小zimu</span>';

        const panel = document.createElement('div');
        panel.id = 'zimu-brain-panel';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', '小zimu 对话');
        panel.innerHTML = `
            <div class="zb-header">
                <div>
                    <div class="zb-title">小zimu</div>
                    <div class="zb-sub" id="zimu-brain-status">第二大脑 · 加载中…</div>
                </div>
                <div class="zb-header-actions">
                    <button type="button" id="zimu-brain-clear" title="清空对话"><i class="fas fa-trash-alt"></i></button>
                    <button type="button" id="zimu-brain-close" title="关闭"><i class="fas fa-times"></i></button>
                </div>
            </div>
            <div id="zimu-brain-messages"></div>
            <div class="zb-footer">
                <textarea id="zimu-brain-input" rows="2" placeholder="问我关于博客的任何问题…" maxlength="2000"></textarea>
                <div class="zb-send-row">
                    <button type="button" id="zimu-brain-send">发送</button>
                </div>
            </div>
        `;

        document.body.appendChild(launcher);
        document.body.appendChild(panel);

        return {
            launcher,
            panel,
            messages: panel.querySelector('#zimu-brain-messages'),
            input: panel.querySelector('#zimu-brain-input'),
            send: panel.querySelector('#zimu-brain-send'),
            status: panel.querySelector('#zimu-brain-status'),
            closeBtn: panel.querySelector('#zimu-brain-close'),
            clearBtn: panel.querySelector('#zimu-brain-clear'),
        };
    }

    const ZimuBrain = {
        ui: null,
        history: [],
        open: false,
        busy: false,
        ready: false,

        init() {
            if (window.location.protocol === 'file:') return;
            this.ui = buildUi();
            if (!this.ui) return;

            this.history = loadHistory();
            this.renderAll();

            this.ui.launcher.addEventListener('click', () => this.togglePanel(true));
            this.ui.closeBtn.addEventListener('click', () => this.togglePanel(false));
            this.ui.clearBtn.addEventListener('click', () => this.clearChat());
            this.ui.send.addEventListener('click', () => this.send());
            this.ui.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.send();
                }
            });

            this.fetchStatus();
        },

        togglePanel(show) {
            this.open = show;
            this.ui.panel.classList.toggle('zb-open', show);
            if (show) {
                this.ui.input.focus();
                if (!this.history.length) {
                    this.appendSystem(
                        '你好，我是小zimu，可以帮你检索并理解本博客已发布的全部文章。有什么想了解的？'
                    );
                }
            }
        },

        async fetchStatus() {
            try {
                const res = await fetch(`${apiRoot()}/brain/status`, { credentials: 'include' });
                const data = await res.json();
                this.ready = !!data.ready;
                const sub = this.ui.status;
                if (!data.configured) {
                    sub.textContent = '未配置 LLM';
                } else if (data.ready) {
                    sub.textContent = `第二大脑 · 已索引 ${data.article_count || 0} 篇文章`;
                } else {
                    sub.textContent = data.message || '知识库准备中';
                }
            } catch {
                this.ui.status.textContent = '第二大脑 · 连接失败';
            }
        },

        clearChat() {
            this.history = [];
            saveHistory(this.history);
            this.ui.messages.innerHTML = '';
            this.appendSystem('对话已清空。继续问我关于博客的问题吧。');
        },

        appendSystem(text) {
            const el = document.createElement('div');
            el.className = 'zb-msg zb-msg-system';
            el.textContent = text;
            this.ui.messages.appendChild(el);
            this.scrollBottom();
        },

        appendMessage(role, content, sources) {
            const el = document.createElement('div');
            el.className = `zb-msg zb-msg-${role}`;
            el.textContent = content;

            if (role === 'assistant' && sources && sources.length) {
                const box = document.createElement('div');
                box.className = 'zb-sources';
                box.innerHTML = '<strong>参考文章</strong>';
                sources.forEach((s) => {
                    const a = document.createElement('a');
                    a.href = `detail.html?id=${encodeURIComponent(s.id)}`;
                    a.textContent = `《${s.title || '无标题'}》`;
                    a.target = '_blank';
                    box.appendChild(a);
                });
                el.appendChild(box);
            }

            this.ui.messages.appendChild(el);
            this.scrollBottom();
        },

        renderAll() {
            this.ui.messages.innerHTML = '';
            this.history.forEach((m) => {
                if (m.role === 'user' || m.role === 'assistant') {
                    this.appendMessage(m.role, m.content, m.sources);
                }
            });
        },

        scrollBottom() {
            const box = this.ui.messages;
            box.scrollTop = box.scrollHeight;
        },

        setBusy(busy) {
            this.busy = busy;
            this.ui.send.disabled = busy;
            this.ui.input.disabled = busy;
            const existing = this.ui.messages.querySelector('.zb-typing');
            if (busy) {
                if (!existing) {
                    const t = document.createElement('div');
                    t.className = 'zb-typing';
                    t.textContent = '小zimu 思考中…';
                    this.ui.messages.appendChild(t);
                    this.scrollBottom();
                }
            } else if (existing) {
                existing.remove();
            }
        },

        async send() {
            const text = this.ui.input.value.trim();
            if (!text || this.busy) return;

            this.ui.input.value = '';
            this.appendMessage('user', text);
            this.history.push({ role: 'user', content: text });
            saveHistory(this.history);

            const apiHistory = this.history
                .filter((m) => m.role === 'user' || m.role === 'assistant')
                .map((m) => ({ role: m.role, content: m.content }));

            this.setBusy(true);
            try {
                const res = await fetch(`${apiRoot()}/brain/chat`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({
                        message: text,
                        history: apiHistory.slice(0, -1),
                        page_context: getPageContext(),
                    }),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.error) {
                    this.appendSystem(data.error || '请求失败，请稍后再试');
                    return;
                }
                const reply = data.reply || '';
                const sources = data.sources || [];
                this.appendMessage('assistant', reply, sources);
                this.history.push({ role: 'assistant', content: reply, sources });
                saveHistory(this.history);
            } catch {
                this.appendSystem('网络异常，请检查连接后重试');
            } finally {
                this.setBusy(false);
            }
        },
    };

    function injectAssets() {
        if (!document.querySelector('link[href*="zimu-brain.css"]')) {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = '/assets/zimu-brain.css';
            document.head.appendChild(link);
        }
    }

    injectAssets();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => ZimuBrain.init());
    } else {
        ZimuBrain.init();
    }

    window.ZimuBrain = ZimuBrain;
})();
