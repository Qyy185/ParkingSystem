const store = require('../../utils/parking-store');

Page({
    data: {
        logs: [],
        viewState: 'ready',
        errorMessage: '',
    },

    onShow() {
        this.load();
    },

    load() {
        const logs = store.getLogs().slice().reverse().map((log, index) => ({
            id: String(log.time) + '-' + index,
            plate: log.plate,
            typeText: log.type === 'in'
                ? '入场'
                : (log.type === 'pay' ? '缴费' : (log.type === 'remove' ? '账户注销' : log.type)),
            spot: log.spot || '—',
            channelText: log.channel === 'member' ? '会员' : '临停',
            feeText: typeof log.fee === 'number' && log.fee > 0 ? log.fee + ' 元' : '—',
            minutesText: typeof log.minutes === 'number' ? store.formatDuration(log.minutes) : '—',
            timeText: store.formatDateTime(log.time),
            noteText: log.reason || '',
        }));

        this.setData({
            logs: logs,
            viewState: logs.length ? 'ready' : 'empty',
        });
    },

    handleRetry() {
        this.setData({ viewState: 'loading' });
        this.load();
    },

    goBack() {
        wx.navigateBack({ delta: 1 });
    },
});
