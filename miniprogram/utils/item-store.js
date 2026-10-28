// 本文件是静态版本地实现：用于在小程序本地快速联调列表增删改。
// 数据上云时，只需把页面里的 store 替换为 cloud-service 的
// miniprogram-data-access.js 中 createTableAccess(cloud, { table, columns }) 返回对象，
// 页面层调用方式保持 list/create/update/remove 不变。

const NOT_FOUND_OR_FORBIDDEN_ERROR = {
    code: 'NOT_FOUND_OR_FORBIDDEN',
    message: '没有保存成功，这条记录可能已被删除或无权修改',
};

const INVALID_ID_ERROR = {
    code: 'INVALID_ID',
    message: '缺少记录 id',
};

const INVALID_PAYLOAD_ERROR = {
    code: 'INVALID_PAYLOAD',
    message: '没有可提交的字段',
};

let idCounter = 0;

function createLocalItemStore(options) {
    const config = options || {};
    if (typeof config.storageKey !== 'string' || config.storageKey.trim() === '') {
        throw new TypeError('storageKey 不能为空');
    }
    const storageKey = config.storageKey;

    return {
        list() {
            return safely(() => {
                const items = readItems(storageKey);
                return { data: items, error: null };
            });
        },

        create(payload) {
            return safely(() => {
                const writable = toCreateRow(payload);
                if (!hasWritableFields(writable)) {
                    return { data: null, error: INVALID_PAYLOAD_ERROR };
                }

                const now = Date.now();
                const item = {
                    id: createItemId(now),
                    title: writable.title,
                    note: writable.note,
                    createdAt: now,
                    updatedAt: now,
                };

                const items = readItems(storageKey);
                items.unshift(item);
                writeItems(storageKey, items);
                return { data: item, error: null };
            });
        },

        update(id, patch) {
            if (isInvalidId(id)) {
                return Promise.resolve({ data: null, error: INVALID_ID_ERROR });
            }
            return safely(() => {
                const writable = toUpdateRow(patch);
                if (!hasWritableFields(writable)) {
                    return { data: null, error: INVALID_PAYLOAD_ERROR };
                }

                const items = readItems(storageKey);
                const index = findIndexById(items, String(id));
                if (index === -1) {
                    return { data: null, error: NOT_FOUND_OR_FORBIDDEN_ERROR };
                }

                const current = items[index];
                const next = {
                    id: current.id,
                    title: writable.title === undefined ? current.title : writable.title,
                    note: writable.note === undefined ? current.note : writable.note,
                    createdAt: current.createdAt,
                    updatedAt: Date.now(),
                };

                items[index] = next;
                writeItems(storageKey, items);
                return { data: next, error: null };
            });
        },

        remove(id) {
            if (isInvalidId(id)) {
                return Promise.resolve({ data: null, error: INVALID_ID_ERROR });
            }
            return safely(() => {
                const itemId = String(id);
                const items = readItems(storageKey);
                const filtered = items.filter(item => item.id !== itemId);
                const removed = items.length - filtered.length;
                if (removed === 0) {
                    return { data: null, error: NOT_FOUND_OR_FORBIDDEN_ERROR };
                }
                writeItems(storageKey, filtered);
                return { data: { removed }, error: null };
            });
        },
    };
}

function safely(executor) {
    return Promise.resolve()
        .then(executor)
        .catch(error => ({ data: null, error: toClientException(error) }));
}

function toClientException(error) {
    const message = error && typeof error === 'object' && typeof error.message === 'string'
        ? error.message
        : '';
    return { code: 'CLIENT_EXCEPTION', message };
}

function hasWritableFields(payload) {
    return payload !== null && typeof payload === 'object' && !Array.isArray(payload) && Object.keys(payload).length > 0;
}

function toCreateRow(payload) {
    const source = isPlainObject(payload) ? payload : {};
    const row = {};
    const title = normalizeTitle(source.title);
    if (title !== '') {
        row.title = title;
    }
    if (typeof source.note === 'string') {
        row.note = source.note.trim();
    } else {
        row.note = '';
    }
    if (row.title === undefined) {
        return {};
    }
    return row;
}

function toUpdateRow(patch) {
    const source = isPlainObject(patch) ? patch : {};
    const row = {};
    if (Object.prototype.hasOwnProperty.call(source, 'title')) {
        const title = normalizeTitle(source.title);
        if (title !== '') {
            row.title = title;
        }
    }
    if (Object.prototype.hasOwnProperty.call(source, 'note') && typeof source.note === 'string') {
        row.note = source.note.trim();
    }
    return row;
}

function normalizeTitle(value) {
    return typeof value === 'string' ? value.trim() : '';
}

function isPlainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function readItems(storageKey) {
    const value = wx.getStorageSync(storageKey);
    if (!Array.isArray(value)) {
        return [];
    }
    return value
        .filter(isStoredItem)
        .sort((left, right) => right.updatedAt - left.updatedAt)
        .map(item => ({
            id: String(item.id),
            title: item.title,
            note: item.note,
            createdAt: item.createdAt,
            updatedAt: item.updatedAt,
        }));
}

function writeItems(storageKey, items) {
    wx.setStorageSync(storageKey, items);
}

function findIndexById(items, itemId) {
    for (let i = 0; i < items.length; i += 1) {
        if (items[i].id === itemId) {
            return i;
        }
    }
    return -1;
}

function createItemId(timestamp) {
    idCounter = (idCounter + 1) % 1000000;
    return String(timestamp) + '-' + String(idCounter);
}

function isInvalidId(id) {
    if (typeof id === 'string') {
        return id.trim() === '';
    }
    if (typeof id === 'number') {
        return !Number.isFinite(id);
    }
    return true;
}

function isStoredItem(item) {
    return item !== null
        && typeof item === 'object'
        && (typeof item.id === 'string' || typeof item.id === 'number')
        && typeof item.title === 'string'
        && typeof item.note === 'string'
        && typeof item.createdAt === 'number'
        && typeof item.updatedAt === 'number';
}

module.exports = {
    createLocalItemStore,
};
