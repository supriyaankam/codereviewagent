export function buildAnalysisBatches(selectedIndices, projectFiles, limits) {
  const maximumRequestFiles = limits.maximum_request_files;
  const maximumBatchBytes = limits.maximum_batch_bytes;
  if (!Number.isInteger(maximumRequestFiles) || maximumRequestFiles < 1) {
    throw new TypeError('The maximum files per analysis request must be a positive integer.');
  }
  if (!Number.isInteger(maximumBatchBytes) || maximumBatchBytes < 1) {
    throw new TypeError('The maximum analysis request size must be a positive integer.');
  }

  const filesByIndex = new Map(projectFiles.map((file) => [file.index, file]));
  const batches = [];
  let batch = [];
  let batchBytes = 0;

  for (const index of selectedIndices) {
    const file = filesByIndex.get(index);
    if (!file) throw new Error('A selected project file is no longer available.');
    if (!Number.isInteger(file.size) || file.size < 0 || file.size > maximumBatchBytes) {
      throw new Error('A selected file exceeds the configured per-request analysis size.');
    }

    if (
      batch.length
      && (batch.length >= maximumRequestFiles || batchBytes + file.size > maximumBatchBytes)
    ) {
      batches.push(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(index);
    batchBytes += file.size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}
