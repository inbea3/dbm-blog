// 后端 Render 地址，部署后替换为实际 URL，例如 'https://dbm-blog-backend.onrender.com'
const DBM_API_BACKEND = 'https://dbm-blog-backend.onrender.com';

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

const MobileMenu = {
    toggle() {
        const menu = document.getElementById('mobile-menu');
        if (menu) {
            menu.classList.toggle('hidden');
        }
    }
};

function apiRoot() {
    return DBM_API_BACKEND ? `${DBM_API_BACKEND}/api` : `${window.location.origin}/api`;
}

const Auth = {
    state: { admin: false, email: null, nickname: null, userId: null, visitor: null },

    async refresh() {
        if (window.location.protocol === 'file:') {
            this.state = { admin: false, email: null, nickname: null, userId: null, visitor: null };
            return this.state;
        }
        try {
            const res = await fetch(`${apiRoot()}/me`, { credentials: 'include' });
            const data = await res.json();
            this.state = {
                admin: !!data?.admin,
                email: data?.email ?? null,
                nickname: data?.nickname ?? null,
                userId: data?.user_id ?? null,
                visitor: data?.visitor ?? null
            };
        } catch (e) {
            this.state = { admin: false, email: null, nickname: null, userId: null, visitor: null };
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
        if (!res.ok) throw new Error(data?.error || '账号或密码错误');
        await this.refresh();
        return data;
    },

    async logout() {
        await fetch(`${apiRoot()}/logout`, { method: 'POST', credentials: 'include' }).catch(() => {});
        await this.refresh();
    },

    requireAdminOrExplain() {
        if (this.state.admin) return true;
        UI.openLoginModal('此操作需要管理员登录：新增/修改/删除文章，或修改「关于我」资料。');
        return false;
    }
};

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
                    <h3 class="text-lg font-bold text-gray-900 dark:text-white" id="login-modal-title">管理员登录</h3>
                    <button type="button" class="text-gray-500 hover:text-gray-700 dark:hover:text-gray-200" data-close="1" aria-label="Close">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <p id="login-hint" class="mt-3 text-sm text-gray-600 dark:text-gray-300"></p>
                <div class="mt-4 space-y-3" id="login-fields-block">
                    <div>
                        <label class="text-sm font-semibold text-gray-700 dark:text-gray-200">账号（邮箱）</label>
                        <input id="login-email" class="mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-gray-900 dark:text-white" placeholder="请输入邮箱" />
                    </div>
                    <div>
                        <label class="text-sm font-semibold text-gray-700 dark:text-gray-200">密码</label>
                        <input id="login-password" type="password" class="mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-gray-900 dark:text-white" placeholder="请输入密码" />
                    </div>
                </div>
                <p id="login-error" class="text-sm text-red-600 mt-2"></p>
                <div class="flex items-center justify-end gap-3 pt-2">
                    <button type="button" class="px-4 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors" data-close="1">
                        取消
                    </button>
                    <button type="button" id="login-submit" class="px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors font-semibold">
                        登录
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        modal.addEventListener('click', (e) => {
            if (e.target?.closest?.('[data-close="1"]')) UI.closeLoginModal();
        });
        document.getElementById('login-submit').addEventListener('click', UI.handleLoginSubmit);
    },

    openLoginModal(hint = '') {
        UI.ensureLoginModal();
        document.getElementById('login-error').textContent = '';
        document.getElementById('login-hint').textContent =
            hint || '仅博主管理员可登录以编辑文章与上传资源；游客可直接评论与互动。';
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
            await refreshAuthorFromApi();
            UI.closeLoginModal();
            UI.applyAuthState();
        } catch (e) {
            err.textContent = e?.message ?? '您作为游客，无法编辑';
        } finally {
            btn.disabled = false;
        }
    },

    applyAuthState() {
        const loggedIn = Auth.state.admin;
        document.querySelectorAll('.admin-only').forEach((el) => {
            el.classList.toggle('hidden', !Auth.state.admin);
        });
        document.querySelectorAll('.guest-only').forEach((el) => {
            el.classList.toggle('hidden', loggedIn);
        });
        // 游客态也显示博主头像（公开 /api/author）；无头像时仍显示 guest 灰色图标
        Author.applyThumbToDom();

        window.dispatchEvent(new CustomEvent('auth-changed', { detail: { ...Auth.state } }));
    },

    installAdminNavGuards() {
        document.addEventListener(
            'click',
            (e) => {
                const el = e.target.closest('[data-require-admin="1"]');
                if (!el) return;
                if (Auth.state.admin) return;
                e.preventDefault();
                const hint =
                    el.getAttribute('data-admin-hint') ||
                    '此操作需要管理员登录：新增/修改/删除文章，或修改「关于我」资料。';
                UI.openLoginModal(hint);
            },
            true
        );
    }
};

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
        return this.get(`/articles/${encodeURIComponent(id)}`);
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
        const url = `${this.baseUrl}/articles/${encodeURIComponent(id)}`;
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
        const url = `${this.baseUrl}/articles/${encodeURIComponent(id)}`;
        const response = await fetch(url, { method: 'DELETE', credentials: 'include' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data?.error || `HTTP ${response.status}`);
        }
        return data;
    },

    async uploadArticleImage(file, articleId) {
        const url = `${this.baseUrl}/uploads/article-image`;
        const form = new FormData();
        form.append('file', file);
        if (articleId) form.append('article_id', String(articleId));
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
    },

    async registerArticleImageUrl(imageUrl, articleId) {
        const body = { url: imageUrl };
        if (articleId) body.article_id = String(articleId);
        const response = await fetch(`${this.baseUrl}/uploads/article-image`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify(body)
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

    async getCategories() {
        return this.get('/categories');
    },

    async getTags() {
        return this.get('/tags');
    },

    async postComment(articleId, body, guestName) {
        const res = await fetch(`${this.baseUrl}/articles/${encodeURIComponent(articleId)}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ body, guest_name: guestName || '' })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
        return data;
    },

    async postReaction(articleId, kind) {
        const res = await fetch(`${this.baseUrl}/articles/${encodeURIComponent(articleId)}/reactions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ kind })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
        return data;
    },

    async setVisitorNickname(nickname) {
        const res = await fetch(`${this.baseUrl}/visitor/nickname`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ nickname })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
        return data;
    }
};

const Contact = {
    data: {
        gitee: '',
        email: '',
        qq: ''
    },

    applyToDom() {
        const { gitee, email, qq } = this.data;

        const gNav = document.getElementById('contact-gitee');
        if (gNav) gNav.href = gitee || '#';

        const gHero = document.getElementById('hero-gitee');
        if (gHero) gHero.href = gitee || '#';

        const gAbout = document.getElementById('about-gitee');
        if (gAbout) gAbout.href = gitee || '#';

        const emailText = document.getElementById('email-text');
        if (emailText) emailText.textContent = email || '未配置';

        const qqText = document.getElementById('qq-text');
        if (qqText) qqText.textContent = qq || '未配置';

        // about.html「联系我」卡片中的 QQ 号（与邮箱卡片一致，来自 profile_contact）
        const qqContactCard = document.getElementById('qq-contact');
        if (qqContactCard) qqContactCard.textContent = qq || '未配置';

        // about.html 的联系信息区
        const emailContact = document.getElementById('email-contact');
        if (emailContact) emailContact.textContent = email || '未配置';

        const githubContact = document.getElementById('github-contact');
        if (githubContact) {
            if (gitee) {
                githubContact.href = gitee;
                githubContact.textContent = '打开链接';
            } else {
                githubContact.removeAttribute('href');
                githubContact.textContent = '未配置';
            }
        }
    },

    async init(seed = {}) {
        if (window.location.protocol === 'file:') {
            this.data = {
                gitee: seed.gitee ?? '',
                email: seed.email ?? '',
                qq: seed.qq ?? ''
            };
            this.applyToDom();
            return null;
        }
        return refreshAuthorFromApi();
    }
};

const Author = {
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
            const guestLink = el.closest('a.guest-only');
            const guestIcon = guestLink?.querySelector('.guest-avatar-btn');

            if (this.data.avatar) {
                el.src = this.data.avatar;
                el.classList.remove('hidden');
                if (guestIcon) guestIcon.classList.add('hidden');
            } else {
                el.removeAttribute('src');
                el.classList.add('hidden');
                if (guestIcon) guestIcon.classList.remove('hidden');
            }
        });
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
        await refreshAuthorFromApi();
        return author;
    }
};

let _authorRefreshInflight = null;

function applyAuthorOptionalFields(author) {
    if (!author) return;
    const nameEl = document.getElementById('author-name');
    if (nameEl) nameEl.textContent = author.name || '';
    const bioEl = document.getElementById('author-bio');
    if (bioEl) bioEl.textContent = author.bio || '';
    const skillsEl = document.getElementById('skills-container');
    if (!skillsEl || !Array.isArray(author.skills)) return;
    skillsEl.innerHTML = '';
    if (!author.skills.length) return;
    skillsEl.classList.add('mt-6', 'max-w-4xl', 'mx-auto');
    author.skills.forEach((skill, index) => {
        const tag = document.createElement('span');
        tag.className = 'skill-tag';
        tag.textContent = skill;
        tag.style.animationDelay = `${index * 0.1}s`;
        skillsEl.appendChild(tag);
    });
}

async function refreshAuthorFromApi() {
    if (_authorRefreshInflight) return _authorRefreshInflight;

    const run = async () => {
        if (window.location.protocol === 'file:') {
            Contact.applyToDom();
            return null;
        }
        try {
            const author = await API.getAuthor();
            const social = author?.social ?? {};
            Contact.data.gitee = social.gitee ?? '';
            Contact.data.email = social.email ?? '';
            Contact.data.qq = social.qq ?? '';
            Contact.applyToDom();
            Author.data.name = author?.name ?? '';
            Author.data.bio = author?.bio ?? '';
            Author.data.avatar = author?.avatar ?? '';
            Author.applyAvatarToDom();
            Author.applyThumbToDom();
            applyAuthorOptionalFields(author);
            document.getElementById('skills-error')?.classList?.add('hidden');
            window.dispatchEvent(new CustomEvent('author-loaded', { detail: author }));
            return author;
        } catch (e) {
            console.error('作者资料加载失败', e);
            const nameEl = document.getElementById('author-name');
            const bioEl = document.getElementById('author-bio');
            if (nameEl) nameEl.textContent = '加载失败';
            if (bioEl) bioEl.textContent = '请通过本站 http(s) 地址访问，并确认数据库与后端已启动。';
            document.getElementById('skills-error')?.classList?.remove('hidden');
            Contact.applyToDom();
            return null;
        }
    };

    _authorRefreshInflight = run().finally(() => {
        _authorRefreshInflight = null;
    });
    return _authorRefreshInflight;
}

document.addEventListener('DOMContentLoaded', () => {
    DarkMode.init();
    UI.installAdminNavGuards();
    Auth.refresh().then(async () => {
        await refreshAuthorFromApi();
        UI.applyAuthState();
    });

    document.querySelectorAll('[data-auth-trigger="1"]').forEach((el) => {
        el.addEventListener('click', (e) => {
            e.preventDefault();
            UI.openLoginModal();
        });
    });
});

window.addEventListener('pageshow', () => {
    Auth.refresh().then(async () => {
        await refreshAuthorFromApi();
        UI.applyAuthState();
    });
});