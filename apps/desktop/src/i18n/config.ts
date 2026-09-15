import i18n from "i18next";
import { initReactI18next } from "react-i18next";

const resources = {
  en: {
    translation: {
      "app.name": "CAN-X",
      "app.tagline": "Agent-native Professional CAN Engineering Workbench",
      "workspace.agent": "Agent",
      "workspace.label": "Engineering workspace",
      "workspace.loading": "Preparing workspace",
      "workspace.plot": "Plot",
      "workspace.trace": "Trace",
      "trace.title": "Trace",
      "trace.follow": "Follow",
      "trace.freeze": "Freeze",
      "trace.column.timestamp": "Timestamp",
      "trace.column.channel": "Channel",
      "trace.column.id": "ID",
      "trace.column.dlc": "DLC",
      "trace.column.data": "Data",
      "trace.column.direction": "Direction",
      "plot.title": "Live byte plot",
      "plot.time": "Time",
      "plot.byte0": "Byte 0",
      "runtime.starting": "Starting Virtual CAN",
      "runtime.capturing": "Virtual CAN active",
      "runtime.failed": "Runtime unavailable",
    },
  },
  "zh-CN": {
    translation: {
      "app.name": "CAN-X",
      "app.tagline": "Agent 原生专业 CAN 工程工作台",
      "workspace.agent": "Agent",
      "workspace.label": "工程工作区",
      "workspace.loading": "正在准备工作区",
      "workspace.plot": "绘图",
      "workspace.trace": "报文追踪",
      "trace.title": "报文追踪",
      "trace.follow": "跟随",
      "trace.freeze": "冻结",
      "trace.column.timestamp": "时间戳",
      "trace.column.channel": "通道",
      "trace.column.id": "ID",
      "trace.column.dlc": "DLC",
      "trace.column.data": "数据",
      "trace.column.direction": "方向",
      "plot.title": "实时字节曲线",
      "plot.time": "时间",
      "plot.byte0": "字节 0",
      "runtime.starting": "正在启动虚拟 CAN",
      "runtime.capturing": "虚拟 CAN 运行中",
      "runtime.failed": "运行时不可用",
    },
  },
} as const;

void i18n.use(initReactI18next).init({
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  lng: "en",
  resources,
});

export { i18n };
