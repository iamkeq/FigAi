export const PRIMARY_MODEL_SETTING = "primary_model";
export const MODEL_ID = /^~?[A-Za-z0-9][A-Za-z0-9._:-]*\/[A-Za-z0-9][A-Za-z0-9._:-]*$/;

export interface ModelControl {
  getPrimaryModel(): string;
  setPrimaryModel(model: string): void;
  resolveModel(model: string): Promise<string | null>;
}
