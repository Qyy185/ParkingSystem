const store = require('../../utils/parking-store');

Page({
    data: {
        // 取景框状态：idle（就绪）/ capturing（识别中）/ done（识别完成）
        stage: 'idle',
        plate: '',
        result: null,
        error: '',
        submitting: false,
    },

    handleScan() {
        this.setData({ stage: 'capturing', plate: '', error: '', result: null });
    },

    handlePlateInput(event) {
        this.setData({ plate: event.detail.value, error: '' });
    },

    handleCancel() {
        this.setData({ stage: 'idle', plate: '' });
    },

    handleConfirm() {
        const plate = this.data.plate;
        if (!plate.trim()) {
            this.setData({ error: '请输入摄像头读到的车牌' });
            return;
        }
        this.setData({ submitting: true });

        store.entry(plate).then(result => {
            this.setData({ submitting: false });
            if (result.error) {
                this.setData({ error: result.error.message });
                return;
            }
            this.setData({
                stage: 'done',
                result: {
                    plate: store.normalizePlate(plate),
                    spot: result.data.spot.id,
                    zone: result.data.spot.zone,
                    channelText: result.data.channel === 'member' ? '会员（不计停车费）' : '临停（按小时计费）',
                    isMember: result.data.channel === 'member',
                    message: result.data.message,
                },
            });
        });
    },

    handleReset() {
        this.setData({ stage: 'idle', plate: '', result: null, error: '' });
    },

    goBack() {
        wx.navigateBack({ delta: 1 });
    },
});
