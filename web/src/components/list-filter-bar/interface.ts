export type FilterType = {
  id: string;
  field?: string;
  label: string | JSX.Element;
  list?: FilterType[];
  value?: string | string[];
  count?: number;
  canSearch?: boolean;
};
export type FilterCollection = {
  field: string;
  label: string;
  list: FilterType[];
  canSearch?: boolean;
};
export interface FilterValue {
  [key: string]: string[] | FilterValue;
}
export type FilterChange = (value: FilterValue) => void;
