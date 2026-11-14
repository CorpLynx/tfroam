import Vue from "vue";
import App from "./App.vue";
import VueCytoscape from "vue-cytoscape";
import VueMeta from "vue-meta";
import "./styles/base.css";

Vue.use(VueCytoscape);
Vue.use(VueMeta);

Vue.config.productionTip = false;

new Vue({
  render: (h) => h(App),
}).$mount("#app");
