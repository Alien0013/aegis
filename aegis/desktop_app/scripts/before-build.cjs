"use strict";

const { writeBuildStamp } = require("./write-build-stamp.cjs");
const { stageBackend } = require("./stage-backend.cjs");

module.exports = async function beforeBuild() {
  writeBuildStamp();
  stageBackend();
  return false;
};
