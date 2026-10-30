import { HomeIcon } from '../svg-icon';

export enum EmptyType {
  Data = 'data',
  SearchData = 'search-data',
}

export enum EmptyCardType {
  Agent = 'agent',
  Dataset = 'dataset',
  Chat = 'chat',
  Search = 'search',
  Memory = 'memory',
  Skills = 'skills',
}

export const EmptyCardData = {
  [EmptyCardType.Agent]: {
    icon: <HomeIcon name="agents" width={'24'} />,
    titleKey: 'empty.agentTitle',
    actionKey: 'empty.createAgent',
    notFoundKey: 'empty.notFoundAgent',
  },
  [EmptyCardType.Dataset]: {
    icon: <HomeIcon name="datasets" width={'24'} />,
    titleKey: 'empty.datasetTitle',
    actionKey: 'empty.createDataset',
    notFoundKey: 'empty.notFoundDataset',
  },
  [EmptyCardType.Chat]: {
    icon: <HomeIcon name="chats" width={'24'} />,
    titleKey: 'empty.chatTitle',
    actionKey: 'empty.createChat',
    notFoundKey: 'empty.notFoundChat',
  },
  [EmptyCardType.Search]: {
    icon: <HomeIcon name="searches" width={'24'} />,
    titleKey: 'empty.searchTitle',
    actionKey: 'empty.createSearch',
    notFoundKey: 'empty.notFoundSearch',
  },
  [EmptyCardType.Memory]: {
    icon: <HomeIcon name="memory" width={'24'} />,
    titleKey: 'empty.memoryTitle',
    actionKey: 'empty.createMemory',
    notFoundKey: 'empty.notFoundMemory',
  },
  [EmptyCardType.Skills]: {
    icon: <HomeIcon name="skills" width={'24'} />,
    titleKey: 'empty.skillsTitle',
    actionKey: 'empty.createSkills',
    notFoundKey: 'empty.notFoundSkills',
  },
};
