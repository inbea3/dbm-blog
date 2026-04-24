/**
 * 共享JavaScript函数库
 * 用于博客网站的通用功能
 */

// 深色模式管理
const DarkMode = {
    toggle() {
        const html = document.documentElement;
        const icon = document.getElementById('theme-icon');

        if (html.classList.contains('dark')) {
            html.classList.remove('dark');
            icon.className = 'fas fa-moon';
            localStorage.setItem('darkMode', 'false');
        } else {
            html.classList.add('dark');
            icon.className = 'fas fa-sun';
            localStorage.setItem('darkMode', 'true');
        }
    },

    init() {
        const darkMode = localStorage.getItem('darkMode');
        if (darkMode === 'true') {
            document.documentElement.classList.add('dark');
            const icon = document.getElementById('theme-icon');
            if (icon) icon.className = 'fas fa-sun';
        }
    }
};

// 移动端菜单管理
const MobileMenu = {
    toggle() {
        const menu = document.getElementById('mobile-menu');
        if (menu) {
            menu.classList.toggle('hidden');
        }
    }
};

// API 根路径：与当前页面同源，避免端口/域名不一致导致请求失败
function apiRoot() {
    return `${window.location.origin}/api`;
}

// 登录/权限
const Auth = {
    state: { admin: false, email: null },

    async refresh() {
        if (window.location.protocol === 'file:') {
            this.state = { admin: false, email: null };
            return this.state;
        }
        try {
            const res = await fetch(`${apiRoot()}/me`, { credentials: 'include' });
            const data = await res.json();
            this.state = { admin: !!data?.admin, email: data?.email ?? null };
        } catch (e) {
            this.state = { admin: false, email: null };
        }
        return this.state;
    },

    async login(email, password) {
        const res = await fetch(`${apiRoot()}/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ email, password })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || '您作为游客，无法编辑');
        await this.refresh();
        return data;
    },

    async logout() {
        await fetch(`${apiRoot()}/logout`, { method: 'POST', credentials: 'include' }).catch(() => {});
        await this.refresh();
    },

    requireAdminOrExplain() {
        if (this.state.admin) return true;
        UI.openLoginModal('您作为游客，无法编辑');
        return false;
    }
};

// UI：登录弹窗 + admin-only 显示控制
const UI = {
    ensureLoginModal() {
        if (document.getElementById('login-modal')) return;
        const modal = document.createElement('div');
        modal.id = 'login-modal';
        modal.className = 'hidden fixed inset-0 z-[1200]';
        modal.innerHTML = `
            <div class="absolute inset-0 bg-black/50" data-close="1"></div>
            <div class="relative max-w-md mx-auto mt-28 bg-white dark:bg-gray-800 rounded-2xl p-6 shadow-xl">
                <div class="flex items-center justify-between">
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white">管理员登录</h3>
                    <button type="button" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200" data-close="1" aria-label="Close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <p id="login-hint" class="mt-3 text-sm text-gray-600 dark:text-gray-300"></p>
                <div class="mt-4 space-y-3">
                    <div>
                        <label class="text-sm font-semibold text-gray-700 dark:text-gray-200">账号（邮箱）</label>
                        <input id="login-email" class="mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-gray-900 dark:text-white" placeholder="请输入邮箱" />
                    </div>
                    <div>
                        <label class="text-sm font-semibold text-gray-700 dark:text-gray-200">密码</label>
                        <input id="login-password" type="password" class="mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-gray-900 dark:text-white" placeholder="请输入密码" />
                    </div>
                    <p id="login-error" class="text-sm text-red-600"></p>
                    <div class="flex items-center justify-end gap-3 pt-2">
                        <button type="button" class="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" data-close="1">
                            取消
                        </button>
                        <button type="button" id="login-submit" class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors font-semibold">
                            登录
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        modal.addEventListener('click', (e) => {
            const t = e.target;
            if (t && t.getAttribute && t.getAttribute('data-close') === '1') UI.closeLoginModal();
        });
        document.getElementById('login-submit').addEventListener('click', UI.handleLoginSubmit);
    },

    openLoginModal(hint = '') {
        UI.ensureLoginModal();
        document.getElementById('login-error').textContent = '';
        document.getElementById('login-hint').textContent = hint || '登录后可编辑/发帖/上传。';
        document.getElementById('login-modal').classList.remove('hidden');
        setTimeout(() => document.getElementById('login-email')?.focus(), 0);
    },

    closeLoginModal() {
        document.getElementById('login-modal')?.classList.add('hidden');
    },

    async handleLoginSubmit() {
        const email = document.getElementById('login-email').value;
        const password = document.getElementById('login-password').value;
        const err = document.getElementById('login-error');
        const btn = document.getElementById('login-submit');
        btn.disabled = true;
        err.textContent = '';
        try {
            await Auth.login(email, password);
            UI.closeLoginModal();
            UI.applyAuthState();
        } catch (e) {
            err.textContent = e?.message ?? '您作为游客，无法编辑';
        } finally {
            btn.disabled = false;
        }
    },

    applyAuthState() {
        // admin-only：管理员才可见
        document.querySelectorAll('.admin-only').forEach((el) => {
            el.classList.toggle('hidden', !Auth.state.admin);
        });
        // guest-only：游客可见
        document.querySelectorAll('.guest-only').forEach((el) => {
            el.classList.toggle('hidden', Auth.state.admin);
        });
        // 头像缩略图：仅管理员显示真实头像；游客保持隐藏
        if (Auth.state.admin) {
            Author.init();
        } else {
            document.querySelectorAll('.author-avatar-thumb').forEach((el) => el.classList.add('hidden'));
        }

        // 通知页面（用于动态列表在登录后刷新显示）
        window.dispatchEvent(new CustomEvent('auth-changed', { detail: { ...Auth.state } }));
    }
};

// API请求封装
const API = {
    get baseUrl() {
        return apiRoot();
    },

    async get(endpoint) {
        try {
            const response = await fetch(`${this.baseUrl}${endpoint}`);
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return await response.json();
        } catch (error) {
            console.error('API请求失败:', error);
            throw error;
        }
    },

    async getArticles() {
        return this.get('/articles');
    },

    async getArticle(id) {
        return this.get(`/articles/${id}`);
    },

    async getAuthor() {
        return this.get('/author');
    },

    async uploadAvatar(file) {
        const url = `${this.baseUrl}/author/avatar`;
        const form = new FormData();
        form.append('file', file);

        const response = await fetch(url, {
            method: 'POST',
            body: form,
            credentials: 'include'
        });

        if (!response.ok) {
            let msg = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                if (data?.error) msg = data.error;
            } catch (e) {
                // ignore
            }
            throw new Error(msg);
        }

        return await response.json();
    },

    async createArticle(payload) {
        const url = `${this.baseUrl}/articles`;
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload ?? {}),
            credentials: 'include'
        });
        if (!response.ok) {
            let msg = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                if (data?.error) msg = data.error;
            } catch (e) {
                // ignore
            }
            throw new Error(msg);
        }
        return await response.json();
    },

    async updateArticle(id, payload) {
        const url = `${this.baseUrl}/articles/${id}`;
        const response = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload ?? {}),
            credentials: 'include'
        });
        if (!response.ok) {
            let msg = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                if (data?.error) msg = data.error;
            } catch (e) {
                // ignore
            }
            throw new Error(msg);
        }
        return await response.json();
    },

    async deleteArticle(id) {
        const url = `${this.baseUrl}/articles/${id}`;
        const response = await fetch(url, { method: 'DELETE', credentials: 'include' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data?.error || `HTTP ${response.status}`);
        }
        return data;
    },

    async uploadArticleImage(file) {
        const url = `${this.baseUrl}/uploads/article-image`;
        const form = new FormData();
        form.append('file', file);
        const response = await fetch(url, { method: 'POST', body: form, credentials: 'include' });
        if (!response.ok) {
            let msg = `HTTP ${response.status}`;
            try {
                const data = await response.json();
                if (data?.error) msg = data.error;
            } catch (e) {
                // ignore
            }
            throw new Error(msg);
        }
        return await response.json();
    }
};

// 联系方式/作者信息（前端不硬编码真实数据）
const Contact = {
    data: {
        gitee: '',
        email: '',
        qq: '',
        qqQr: ''
    },

    defaultQrPath() {
        // 页面都在 frontend/ 下：file:// 时用相对路径；走服务端时用 /assets/
        return window.location.protocol === 'file:' ? '../assets/qq_qr.svg' : '/assets/qq_qr.svg';
    },

    applyToDom() {
        const { gitee, email, qq, qqQr } = this.data;

        const gNav = document.getElementById('contact-gitee');
        if (gNav && gitee) gNav.href = gitee;

        const gHero = document.getElementById('hero-gitee');
        if (gHero && gitee) gHero.href = gitee;

        const gAbout = document.getElementById('about-gitee');
        if (gAbout && gitee) gAbout.href = gitee;

        const emailText = document.getElementById('email-text');
        if (emailText && email) emailText.textContent = email;

        const qqText = document.getElementById('qq-text');
        if (qqText && qq) qqText.textContent = qq;

        const qr = document.getElementById('qq-qr');
        if (qr) qr.src = qqQr || this.defaultQrPath();

        // about.html 的联系信息区
        const emailContact = document.getElementById('email-contact');
        if (emailContact && email) emailContact.textContent = email;

        const githubContact = document.getElementById('github-contact');
        if (githubContact && gitee) {
            githubContact.href = gitee;
            githubContact.textContent = gitee;
        }

        const twitterContact = document.getElementById('twitter-contact');
        if (twitterContact) twitterContact.textContent = '点击查看二维码';
    },

    async loadFromApi() {
        if (window.location.protocol === 'file:') {
            // file:// 无法同源请求后端，直接用占位/二维码默认值
            this.data.qqQr ||= this.defaultQrPath();
            this.applyToDom();
            return;
        }

        try {
            const author = await API.getAuthor();
            const social = author?.social ?? {};
            this.data.gitee = social.gitee ?? this.data.gitee;
            this.data.email = social.email ?? this.data.email;
            this.data.qq = social.qq ?? this.data.qq;
            this.data.qqQr = social.qqQr ?? this.data.qqQr ?? this.defaultQrPath();
        } catch (e) {
            // 静默失败：不影响页面其它功能
            this.data.qqQr ||= this.defaultQrPath();
        } finally {
            this.applyToDom();
        }
    },

    init(seed = {}) {
        this.data = {
            gitee: seed.gitee ?? '',
            email: seed.email ?? '',
            qq: seed.qq ?? '',
            qqQr: seed.qqQr ?? ''
        };

        if (!this.data.qqQr) this.data.qqQr = this.defaultQrPath();
        this.applyToDom();
        this.loadFromApi();
    }
};

// 作者信息（头像等）
const Author = {
    _initialized: false,
    data: {
        name: '',
        bio: '',
        avatar: ''
    },

    applyAvatarToDom() {
        const img = document.getElementById('author-avatar');
        const fallback = document.getElementById('author-avatar-fallback');
        if (!img) return;

        if (this.data.avatar) {
            img.src = this.data.avatar;
            img.classList.remove('hidden');
            if (fallback) fallback.classList.add('hidden');
        } else {
            img.removeAttribute('src');
            img.classList.add('hidden');
            if (fallback) fallback.classList.remove('hidden');
        }
    },

    applyThumbToDom() {
        const thumbs = document.querySelectorAll('.author-avatar-thumb');
        if (!thumbs?.length) return;

        thumbs.forEach((el) => {
            if (!(el instanceof HTMLImageElement)) return;
            if (this.data.avatar) {
                el.src = this.data.avatar;
                el.classList.remove('hidden');
            } else {
                el.removeAttribute('src');
                el.classList.add('hidden');
            }
        });
    },

    async loadFromApi() {
        if (window.location.protocol === 'file:') return;
        try {
            const author = await API.getAuthor();
            this.data.name = author?.name ?? this.data.name;
            this.data.bio = author?.bio ?? this.data.bio;
            this.data.avatar = author?.avatar ?? this.data.avatar;
        } catch (e) {
            // ignore
        } finally {
            this.applyAvatarToDom();
            this.applyThumbToDom();
        }
    },

    init() {
        if (this._initialized) return;
        this._initialized = true;
        this.applyAvatarToDom();
        this.applyThumbToDom();
        this.loadFromApi();
    },

    async uploadFromInput(inputEl) {
        const file = inputEl?.files?.[0];
        if (!file) throw new Error('请选择图片文件');
        const author = await API.uploadAvatar(file);
        this.data.avatar = author?.avatar ?? this.data.avatar;
        this.applyAvatarToDom();
        // 避免缓存：追加时间戳
        const img = document.getElementById('author-avatar');
        if (img?.src) img.src = `${img.src.split('?')[0]}?t=${Date.now()}`;
        return author;
    }
};

// 页面初始化
document.addEventListener('DOMContentLoaded', () => {
    DarkMode.init();
    // 初始化权限态（默认游客）
    Auth.refresh().then(() => {
        UI.applyAuthState();
    });

    // 拦截“关于我”导航：游客点击弹出登录弹窗
    document.querySelectorAll('a[href="about.html"]').forEach((a) => {
        a.addEventListener('click', (e) => {
            if (Auth.state.admin) return;
            e.preventDefault();
            UI.openLoginModal('您作为游客，无法编辑');
        });
    });

    // 小灰人按钮：打开登录弹窗
    document.querySelectorAll('[data-auth-trigger="1"]').forEach((el) => {
        el.addEventListener('click', (e) => {
            e.preventDefault();
            UI.openLoginModal();
        });
    });
});

// 处理浏览器“返回上一页”(bfcache) 场景：页面恢复但脚本不重新初始化
window.addEventListener('pageshow', () => {
    Auth.refresh().then(() => {
        UI.applyAuthState();
    });
});