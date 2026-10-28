const store = require('../../utils/parking-store');

Page({
    data: {
        plate: '',
        kind: 'monthly',
        periods: 1,
        packages: store.PACKAGES,
        // 套餐和档位的展示数据
        kindOptions: [],
        periodOptions: [],
        selectedPrice: 0,
        selectedLabel: '',
        current: null,
        submitting: false,
        error: '',
        success: '',
        viewState: 'ready',
    },

    onLoad() {
        this.buildOptions();
    },

    buildOptions() {
        const kindOptions = [];
        const pkg = store.PACKAGES;
        kindOptions.push({
            kind: 'monthly',
            label: pkg.monthly.label,
            desc: '按月订阅，不计停车费',
            priceText: '300 元起 / 月',
        });
        kindOptions.push({
            kind: 'yearly',
            label: pkg.yearly.label,
            desc: '按年订阅，更划算',
            priceText: '2700 元 / 年',
        });
        this.setData({ kindOptions: kindOptions });
        this.syncPeriods(this.data.kind);
    },

    syncPeriods(kind) {
        const options = store.PACKAGES[kind].options.map(item => ({
            periods: item.periods,
            label: item.label,
            priceText: item.price + ' 元',
        }));
        const first = store.PACKAGES[kind].options[0];
        this.setData({
            periodOptions: options,
            periods: first.periods,
            selectedPrice: first.price,
            selectedLabel: first.label,
        });
    },

    handlePlateInput(event) {
        this.setData({ plate: event.detail.value, error: '' });
    },

    handleKindChange(event) {
        const kind = event.currentTarget.dataset.kind;
        this.setData({ kind: kind });
        this.syncPeriods(kind);
    },

    handlePeriodChange(event) {
        const periods = Number(event.currentTarget.dataset.periods);
        const pkg = store.PACKAGES[this.data.kind];
        let option = pkg.options[0];
        pkg.options.forEach(item => {
            if (item.periods === periods) {
                option = item;
            }
        });
        this.setData({
            periods: periods,
            selectedPrice: option.price,
            selectedLabel: option.label,
        });
    },

    handleRecharge() {
        const plate = this.data.plate;
        if (!plate.trim()) {
            this.setData({ error: '请输入车牌号' });
            return;
        }
        if (!store.isValidPlate(plate)) {
            this.setData({ error: '车牌格式不正确，示例：黑A12345' });
            return;
        }
        this.setData({ submitting: true, error: '' });

        store.recharge(plate, this.data.kind, this.data.periods).then(result => {
            this.setData({ submitting: false });
            if (result.error) {
                this.setData({ error: result.error.message });
                return;
            }
            const account = result.data.account;
            this.setData({
                success: result.data.message,
                plate: '',
                current: Object.assign({}, account, {
                    expiresAtText: store.formatDate(account.expiresAt),
                }),
            });
        });
    },

    goBack() {
        wx.navigateBack({ delta: 1 });
    },
});
