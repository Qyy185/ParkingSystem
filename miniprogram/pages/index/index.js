const store = require('../../utils/parking-store');

Page({
    data: {
        // 车位
        spots: [],
        freeCount: 0,
        occupiedCount: 0,
        // 表单
        plate: '',
        // 查费结果
        feeInfo: null,
        // 提示
        message: '',
        messageType: '',
        // 三态
        viewState: 'ready',
        // 底部弹层：入场 / 查费
        showSheet: false,
        sheetMode: 'entry',
        submitting: false,
        formError: '',
        // 流水
        logs: [],
    },

    onShow() {
        this.refresh();
    },

    refresh() {
        const spots = store.getSpots();
        const free = spots.filter(s => s.status === 'free').length;
        const logs = store.getLogs().slice(-8).reverse().map(log => ({
            id: String(log.time),
            plate: log.plate,
            typeText: log.type === 'in' ? '入场' : (log.type === 'pay' ? '缴费' : (log.type === 'remove' ? '注销' : log.type)),
            spot: log.spot,
            channelText: log.channel === 'member' ? '会员' : '临停',
            feeText: typeof log.fee === 'number' && log.fee > 0 ? log.fee + ' 元' : '',
            minutesText: typeof log.minutes === 'number' ? store.formatDuration(log.minutes) : '',
            timeText: store.formatDateTime(log.time),
        }));

        this.setData({
            spots: spots.map(s => ({
                id: s.id,
                status: s.status,
                plate: s.plate || '空闲',
                channelText: s.channel === 'member' ? '会员' : (s.channel === 'temp' ? '临停' : ''),
            })),
            freeCount: free,
            occupiedCount: spots.length - free,
            logs: logs,
        });
    },

    // ---- 打开底部弹层 ----
    openEntry() {
        this.setData({
            showSheet: true,
            sheetMode: 'entry',
            formError: '',
            plate: '',
        });
    },

    openExit() {
        this.setData({
            showSheet: true,
            sheetMode: 'exit',
            formError: '',
            plate: '',
        });
    },

    closeSheet() {
        if (this.data.submitting) {
            return;
        }
        this.setData({ showSheet: false, formError: '' });
    },

    handlePlateInput(event) {
        this.setData({ plate: event.detail.value, formError: '' });
    },

    // ---- 提交：入场或查费 ----
    async handleSubmit() {
        const plate = this.data.plate;
        if (!plate.trim()) {
            this.setData({ formError: '请输入车牌号' });
            return;
        }
        this.setData({ submitting: true });

        if (this.data.sheetMode === 'entry') {
            const result = await store.entry(plate);
            this.setData({ submitting: false });
            if (result.error) {
                this.setData({ formError: result.error.message });
                return;
            }
            this.setData({
                showSheet: false,
                message: result.data.message,
                messageType: 'ok',
                plate: '',
            });
            this.refresh();
            return;
        }

        // 查费
        const result = await store.exit(plate);
        this.setData({ submitting: false });
        if (result.error) {
            this.setData({ formError: result.error.message });
            return;
        }
        const info = result.data;
        this.setData({
            showSheet: false,
            plate: '',
            feeInfo: {
                plate: store.normalizePlate(plate),
                fee: info.fee,
                minutesText: store.formatDuration(info.minutes),
                channelText: info.channel === 'member' ? '会员（不计停车费）' : '临停（按小时计费）',
                isMember: info.channel === 'member',
            },
        });
    },

    // ---- 确认缴费 ----
    async handlePay() {
        const info = this.data.feeInfo;
        if (!info) {
            return;
        }
        this.setData({ submitting: true });
        const result = await store.pay(info.plate);
        this.setData({ submitting: false });

        if (result.error) {
            this.setData({ message: result.error.message, messageType: 'err' });
            return;
        }
        this.setData({
            feeInfo: null,
            message: result.data.message,
            messageType: 'ok',
        });
        this.refresh();
    },

    handleCancelPay() {
        this.setData({ feeInfo: null });
    },

    // ---- 跳转 ----
    goRecharge() {
        wx.navigateTo({ url: '/pages/recharge/recharge' });
    },

    goRemind() {
        wx.navigateTo({ url: '/pages/remind/remind' });
    },

    goLogs() {
        wx.navigateTo({ url: '/pages/item-list/item-list' });
    },

    goScan() {
        wx.navigateTo({ url: '/pages/scan/scan' });
    },

    // ---- 重置演示数据 ----
    handleReset() {
        wx.showModal({
            title: '重置演示数据',
            content: '会清空车位、流水和账户，回到初始状态。确定吗？',
            success: async res => {
                if (!res.confirm) {
                    return;
                }
                await store.resetAll();
                this.setData({ message: '已重置为初始状态', messageType: 'ok', feeInfo: null });
                this.refresh();
            },
        });
    },
});
