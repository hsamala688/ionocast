// Which TEC map the globe shows, shared by the panel switch and the globe:
// 'off' (no overlay), 'predicted' (model), 'actual' (observed IONEX), or
// 'difference' (predicted − actual, the model's error at each cell).
export type TecMode = 'off' | 'predicted' | 'actual' | 'difference';
