const store = require('../../utils/parking-store');

Page({
    data: {
        withinDays: 7,
        rangeOptions: [
            { days: 3, label: '未来 3 天' },
            { days: 7, label: '未来 7 天' },
            { days: 15, label: '未来 15 天' },
            { days: 30, label: '未来 30 天' },
        ],
        pending: [],
        stats: { pending: 0, unsent: 0, sent: 0, noContact: 0 },
        message: '',
        messageType: '',
        submitting: false,
        viewState: 'ready',
    },

    onShow() {
        this.refresh();
    },

    refresh() {
        store.pendingReminders(this.data.withinDays).then(result => {
            if (result.error) {
                this.setData({ viewState: 'error', message: result.error.message, messageType: 'err' });
                return;
            }
            const list = result.data;
            const noContact = list.filter(item => !item.phone && !item.wechat).length;
            this.setData({
                pending: list,
                stats: {
                    pending: list.length,
                    unsent: list.length,
                    sent: 0,
                    noContact: noContact,
                },
                viewState: list.length ? 'ready' : 'empty',
            });
        });
    },

    handleRangeChange(event) {
        const days = Number(event.currentTarget.dataset.days);
        this.setData({ withinDays: days });
        this.refresh();
    },

    // 发送催缴（本地版：只记录，不真发短信）
    handleRemind(event) {
        const plate = event.currentTarget.dataset.plate;
        this.setData({ submitting: true, message: '', messageType: '' });

        // 本地版没有真短信通道，这里模拟「已发送」并写入本地通知记录
        const notice = {
            channel: 'sms',
            target: '本地模拟',
            content: '【停车缴费】您的月租即将到期，请及时续费。',
            plate: plate,
            time: Date.now(),
            status: 'simulated',
        };
        const notices = wx.getStorageSync('parking_notices_v1') || [];
        notices.push(notice);
        wx.setStorageSync('parking_notices_v1', notices);

        this.setData({
            submitting: false,
            message: '已给 ' + plate + ' 发送催缴（本地模拟，未真发短信）',
            messageType: 'ok',
        });
        this.refresh();
    },

    handleRetry() {
        this.setData({ viewState: 'loading' });
        this.refresh();
    },

    goBack() {
        wx.navigateBack({ delta: 1 });
    },
});
