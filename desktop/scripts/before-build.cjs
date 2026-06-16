"use strict";

const { writeBuildStamp } = require("./write-build-stamp.cjs");

module.exports = async function beforeBuild() {
  writeBuildStamp();
  return false;
};
