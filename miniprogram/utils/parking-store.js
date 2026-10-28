// 停车场小程序 · 本地数据层
// ============================================================
//
// 数据存在手机本地（wx.setStorageSync）。这是静态实现，
// 好处是：不用服务器、不用备案，小程序能直接跑起来演示。
// 代价是：数据只在这台手机上，换手机看就是空的。
//
// ★ 接口约定和 cloud-service 的 createTableAccess 完全一致：
//   list() / create() / update() / remove() 全部返回 { data, error }
//   永远不 reject，错误用 error 对象表达。
//   → 将来数据上云时，页面代码一行都不用改，只换这个文件。
//
// ============================================================

const STORAGE_KEYS = {
    // 车位状态：16 个车位
    spots: 'parking_spots_v1',
    // 进出场流水
    logs: 'parking_logs_v1',
    // 会员账户
    accounts: 'parking_accounts_v1',
    // 充值记录
    recharges: 'parking_recharges_v1',
};

// ---- 计费规则（和 Flask 版保持一致）----
const FREE_MINUTES = 30;
const HOUR_PRICE = 5;
const DAILY_CAP = 40;
const MINUTES_PER_DAY = 24 * 60;

const DAY_MS = 24 * 60 * 60 * 1000;
const GRACE_DAYS = 180;      // 宽限期：半年

const ERROR_MESSAGES = {
    NOT_FOUND_OR_FORBIDDEN: '没有保存成功，这条记录可能已被删除',
    INVALID_ID: '缺少记录 id',
    INVALID_PAYLOAD: '没有可提交的字段',
    CLIENT_EXCEPTION: '本地存储读写失败',
};

// ---------------------------------------------------------
//  基础工具
// ---------------------------------------------------------
function safely(executor) {
    return Promise.resolve()
        .then(executor)
        .catch(error => ({
            data: null,
            error: {
                code: 'CLIENT_EXCEPTION',
                message: (error && error.message) || ERROR_MESSAGES.CLIENT_EXCEPTION,
            },
        }));
}

function readArray(key) {
    const value = wx.getStorageSync(key);
    return Array.isArray(value) ? value : [];
}

function writeArray(key, items) {
    wx.setStorageSync(key, items);
}

// ---------------------------------------------------------
//  车牌
// ---------------------------------------------------------
// 中国车牌：省份简称 + 字母 + 5~6 位
const PLATE_PATTERN = /^[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领A-Z][A-Z][A-HJ-NP-Z0-9]{4,5}[A-HJ-NP-Z0-9挂学警港澳]$/;

function normalizePlate(value) {
    if (typeof value !== 'string') {
        return '';
    }
    // 全角转半角、去空格和连字符、大写
    let text = value.replace(/[！-～]/g, ch => String.fromCharCode(ch.charCodeAt(0) - 0xFEE0));
    text = text.replace(/[\s\-_]/g, '').toUpperCase();
    return text;
}

function isValidPlate(value) {
    return PLATE_PATTERN.test(normalizePlate(value));
}

// ---------------------------------------------------------
//  车位
// ---------------------------------------------------------
function initSpots() {
    // v0.4.2：64 个车位，取消分区（后端同步改动）
    const spots = [];
    for (let i = 1; i <= 64; i += 1) {
        spots.push({
            id: 'P-' + String(i).padStart(2, '0'),
            status: 'free',
            plate: null,
            entryTime: null,
            channel: null,
        });
    }
    return spots;
}

function getSpots() {
    const spots = readArray(STORAGE_KEYS.spots);
    if (!spots.length) {
        const fresh = initSpots();
        writeArray(STORAGE_KEYS.spots, fresh);
        return fresh;
    }
    return spots;
}

function saveSpots(spots) {
    writeArray(STORAGE_KEYS.spots, spots);
}

// ---------------------------------------------------------
//  账户
// ---------------------------------------------------------
function getAccounts() {
    return readArray(STORAGE_KEYS.accounts);
}

function saveAccounts(accounts) {
    writeArray(STORAGE_KEYS.accounts, accounts);
}

function findAccount(plateNo) {
    const target = normalizePlate(plateNo);
    if (!target) {
        return null;
    }
    const accounts = getAccounts();
    for (let i = 0; i < accounts.length; i += 1) {
        if (normalizePlate(accounts[i].plate) === target) {
            return accounts[i];
        }
    }
    return null;
}

function isExpired(account) {
    if (!account || !account.expiresAt) {
        return false;
    }
    return Date.now() > account.expiresAt;
}

// 宽限期：过期后还保留 180 天
function isObsolete(account) {
    if (!isExpired(account) || !account.expiresAt) {
        return false;
    }
    return Date.now() > account.expiresAt + GRACE_DAYS * DAY_MS;
}

function classify(plateNo) {
    const account = findAccount(plateNo);
    if (!account) {
        return { kind: 'temporary', account: null };
    }
    if (account.kind !== 'monthly' && account.kind !== 'yearly') {
        return { kind: 'temporary', account };
    }
    if (isExpired(account)) {
        return { kind: 'temporary', account };
    }
    return { kind: account.kind, account };
}

// ---------------------------------------------------------
//  计费
// ---------------------------------------------------------
// 按天分段：每天独立算「免费 30 分钟 → 整小时 → 当天封顶 40」，然后累加。
// 注意是「每天封顶」不是「总额封顶」，停 3 天 = 120 元。
function calcFee(entryTime) {
    const minutes = Math.floor((Date.now() - entryTime) / 60000);
    if (minutes <= FREE_MINUTES) {
        return 0;
    }
    let total = 0;
    let remaining = minutes;
    while (remaining > FREE_MINUTES) {
        const dayMinutes = Math.min(remaining, MINUTES_PER_DAY);
        const billable = dayMinutes - FREE_MINUTES;
        const hours = Math.ceil(billable / 60);
        const dayFee = hours * HOUR_PRICE;
        total += Math.min(dayFee, DAILY_CAP);
        remaining -= dayMinutes;
    }
    return total;
}

function formatDuration(minutes) {
    if (typeof minutes !== 'number' || minutes < 0) {
        return '';
    }
    if (minutes < 60) {
        return minutes + ' 分钟';
    }
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return m ? h + ' 小时 ' + m + ' 分钟' : h + ' 小时';
}

function formatDateTime(ts) {
    if (!ts) {
        return '';
    }
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
        + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

function formatDate(ts) {
    if (!ts) {
        return '永久有效';
    }
    const d = new Date(ts);
    const pad = n => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

// ---------------------------------------------------------
//  流水
// ---------------------------------------------------------
function getLogs() {
    return readArray(STORAGE_KEYS.logs);
}

function addLog(entry) {
    const logs = getLogs();
    logs.push(Object.assign({ time: Date.now() }, entry));
    writeArray(STORAGE_KEYS.logs, logs);
}

function getRecharges() {
    return readArray(STORAGE_KEYS.recharges);
}

function addRecharge(entry) {
    const records = getRecharges();
    records.push(Object.assign({ time: Date.now() }, entry));
    writeArray(STORAGE_KEYS.recharges, records);
}

// ============================================================
//  业务操作
// ============================================================

//  入场
function entry(plateNo) {
    return safely(() => {
        if (!isValidPlate(plateNo)) {
            return { data: null, error: { code: 'INVALID_PAYLOAD', message: '车牌格式不正确，示例：黑A12345' } };
        }
        const normalized = normalizePlate(plateNo);
        const spots = getSpots();

        // 查重
        for (let i = 0; i < spots.length; i += 1) {
            if (spots[i].status === 'occupied' && normalizePlate(spots[i].plate) === normalized) {
                return {
                    data: null,
                    error: { code: 'INVALID_PAYLOAD', message: '这辆车已在场内，车位 ' + spots[i].id },
                };
            }
        }

        // 按车牌分流
        const info = classify(normalized);
        const channel = info.kind === 'temporary' ? 'temp' : 'member';

        // 分配车位
        let target = null;
        for (let i = 0; i < spots.length; i += 1) {
            if (spots[i].status === 'free') {
                target = spots[i];
                break;
            }
        }
        if (!target) {
            return { data: null, error: { code: 'INVALID_PAYLOAD', message: '车位已满' } };
        }

        target.status = 'occupied';
        target.plate = normalized;
        target.entryTime = Date.now();
        target.channel = channel;
        saveSpots(spots);

        addLog({ type: 'in', plate: normalized, spot: target.id, channel: channel });

        // 临停车自动建临时账号（会员不动）
        if (channel === 'temp' && !info.account) {
            const accounts = getAccounts();
            accounts.push({
                plate: normalized,
                kind: 'temporary',
                holder: '',
                note: '临停车辆，入场自动登记',
                balance: 0,
                expiresAt: 0,
                createdAt: Date.now(),
            });
            saveAccounts(accounts);
        }

        return {
            data: {
                spot: target,
                channel: channel,
                kind: info.kind,
                message: channel === 'member'
                    ? '入场成功，车位 ' + target.id + '，会员不计停车费'
                    : '入场成功，车位 ' + target.id,
            },
            error: null,
        };
    });
}

//  出场算钱（只算钱不放行）
function exit(plateNo) {
    return safely(() => {
        const normalized = normalizePlate(plateNo);
        const spots = getSpots();
        for (let i = 0; i < spots.length; i += 1) {
            const spot = spots[i];
            if (spot.status === 'occupied' && normalizePlate(spot.plate) === normalized) {
                const minutes = Math.floor((Date.now() - spot.entryTime) / 60000);
                const channel = spot.channel || 'temp';
                const fee = channel === 'member' ? 0 : calcFee(spot.entryTime);
                return {
                    data: { fee: fee, minutes: minutes, channel: channel, spot: spot },
                    error: null,
                };
            }
        }
        return { data: null, error: { code: 'NOT_FOUND_OR_FORBIDDEN', message: '没找到这辆车' } };
    });
}

//  缴费放行
function pay(plateNo) {
    return safely(() => {
        const normalized = normalizePlate(plateNo);
        const spots = getSpots();
        for (let i = 0; i < spots.length; i += 1) {
            const spot = spots[i];
            if (spot.status !== 'occupied' || normalizePlate(spot.plate) !== normalized) {
                continue;
            }
            const minutes = Math.floor((Date.now() - spot.entryTime) / 60000);
            const channel = spot.channel || 'temp';
            const fee = channel === 'member' ? 0 : calcFee(spot.entryTime);

            spot.status = 'free';
            spot.plate = null;
            spot.entryTime = null;
            spot.channel = null;
            saveSpots(spots);
            addLog({
                type: 'pay', plate: normalized, spot: spot.id,
                minutes: minutes, fee: fee, channel: channel,
            });

            // 清理账户
            let removed = false;
            const accounts = getAccounts();
            const kept = [];
            for (let j = 0; j < accounts.length; j += 1) {
                const acc = accounts[j];
                if (normalizePlate(acc.plate) !== normalized) {
                    kept.push(acc);
                    continue;
                }
                if (acc.kind === 'temporary') {
                    removed = true;            // 临时账号：出场即删
                } else if (isObsolete(acc)) {
                    removed = true;            // 过宽限期的会员账户：注销
                    addLog({
                        type: 'remove', plate: normalized, spot: spot.id,
                        fee: 0, channel: channel, balance: acc.balance || 0,
                        reason: '租期过期且已过宽限期，出场时账户注销',
                    });
                } else {
                    kept.push(acc);            // 宽限期内 / 有效会员：保留
                }
            }
            saveAccounts(kept);

            return {
                data: {
                    fee: fee,
                    channel: channel,
                    minutes: minutes,
                    accountRemoved: removed,
                    message: channel === 'member'
                        ? '会员车辆出场，车位已释放（本次费用 0 元）'
                        : '缴费成功，车位已释放',
                },
                error: null,
            };
        }
        return { data: null, error: { code: 'NOT_FOUND_OR_FORBIDDEN', message: '没找到这辆车' } };
    });
}

//  充值（开通 / 续费会员）
const PACKAGES = {
    monthly: {
        label: '月租套餐',
        options: [
            { periods: 1, label: '1 个月', price: 300 },
            { periods: 3, label: '3 个月', price: 850 },
            { periods: 6, label: '6 个月', price: 1600 },
            { periods: 12, label: '12 个月', price: 3000 },
        ],
    },
    yearly: {
        label: '年租套餐',
        options: [
            { periods: 1, label: '1 年', price: 2700 },
        ],
    },
};

function recharge(plateNo, kind, periods) {
    return safely(() => {
        if (!isValidPlate(plateNo)) {
            return { data: null, error: { code: 'INVALID_PAYLOAD', message: '车牌格式不正确，示例：黑A12345' } };
        }
        if (kind !== 'monthly' && kind !== 'yearly') {
            return { data: null, error: { code: 'INVALID_PAYLOAD', message: '请选择月租或年租套餐' } };
        }
        const pkg = PACKAGES[kind];
        let option = null;
        pkg.options.forEach(item => {
            if (item.periods === Number(periods)) {
                option = item;
            }
        });
        if (!option) {
            return { data: null, error: { code: 'INVALID_PAYLOAD', message: '没有这个套餐档位' } };
        }

        const normalized = normalizePlate(plateNo);
        const accounts = getAccounts();
        let target = null;
        for (let i = 0; i < accounts.length; i += 1) {
            if (normalizePlate(accounts[i].plate) === normalized) {
                target = accounts[i];
                break;
            }
        }

        const now = Date.now();
        const days = option.periods * (kind === 'yearly' ? 365 : 30);
        let created = false;
        let restored = false;

        if (!target) {
            target = {
                plate: normalized, kind: kind, holder: '', note: '充值开通',
                balance: 0, expiresAt: 0, createdAt: now,
            };
            accounts.push(target);
            created = true;
        } else if (target.kind === 'temporary') {
            target.kind = kind;
            target.note = '临停转会员';
        }

        const oldExpires = target.expiresAt || 0;
        const base = oldExpires > now ? oldExpires : now;
        target.expiresAt = base + days * DAY_MS;
        target.balance = (target.balance || 0) + option.price;
        target.kind = kind;
        restored = isExpired(target) === false && target.expiresAt > now && oldExpires > 0 && oldExpires <= now;
        saveAccounts(accounts);

        addRecharge({
            plate: normalized, kind: kind, option: option.label,
            amount: option.price, days: days,
        });

        return {
            data: {
                account: target,
                amount: option.price,
                days: days,
                created: created,
                message: '充值成功 ' + option.price + ' 元，余额 ' + target.balance
                    + ' 元，有效期至 ' + formatDate(target.expiresAt),
            },
            error: null,
        };
    });
}

//  催缴：找出 N 天内到期的会员账户
function pendingReminders(withinDays) {
    return safely(() => {
        const days = withinDays || 7;
        const accounts = getAccounts();
        const now = Date.now();
        const result = [];
        accounts.forEach(acc => {
            if (acc.kind !== 'monthly' && acc.kind !== 'yearly') {
                return;
            }
            if (!acc.expiresAt || now > acc.expiresAt) {
                return;    // 永久有效 / 已过期（宽限期内不催）
            }
            const left = Math.ceil((acc.expiresAt - now) / DAY_MS);
            if (left > days) {
                return;
            }
            result.push({
                plate: acc.plate,
                holder: acc.holder || '车主',
                kind: acc.kind,
                daysLeft: left,
                expireDate: formatDate(acc.expiresAt),
                phone: acc.phone || '',
                wechat: acc.wechat || '',
            });
        });
        result.sort((a, b) => a.daysLeft - b.daysLeft);
        return { data: result, error: null };
    });
}

//  补联系方式
function setContact(plateNo, phone, wechat) {
    return safely(() => {
        const normalized = normalizePlate(plateNo);
        const accounts = getAccounts();
        for (let i = 0; i < accounts.length; i += 1) {
            if (normalizePlate(accounts[i].plate) === normalized) {
                if (phone) {
                    accounts[i].phone = String(phone).trim();
                }
                if (wechat) {
                    accounts[i].wechat = String(wechat).trim();
                }
                saveAccounts(accounts);
                return {
                    data: { account: accounts[i], message: '已保存 ' + normalized + ' 的联系方式' },
                    error: null,
                };
            }
        }
        return { data: null, error: { code: 'NOT_FOUND_OR_FORBIDDEN', message: '这个车牌没有账户' } };
    });
}

//  重置演示数据
function resetAll() {
    return safely(() => {
        saveSpots(initSpots());
        writeArray(STORAGE_KEYS.logs, []);
        writeArray(STORAGE_KEYS.accounts, []);
        writeArray(STORAGE_KEYS.recharges, []);
        return { data: { message: '已重置为初始状态' }, error: null };
    });
}

module.exports = {
    STORAGE_KEYS,
    PACKAGES,
    GRACE_DAYS,
    normalizePlate,
    isValidPlate,
    initSpots,
    getSpots,
    saveSpots,
    getAccounts,
    saveAccounts,
    findAccount,
    isExpired,
    isObsolete,
    classify,
    calcFee,
    formatDuration,
    formatDateTime,
    formatDate,
    getLogs,
    getRecharges,
    entry,
    exit,
    pay,
    recharge,
    pendingReminders,
    setContact,
    resetAll,
};
