import { getLabelForTool, getIconForTool } from './utils';
import { Terminal, Wrench, Search, FileText, Globe, MousePointerClick, Puzzle } from 'lucide-react'; // Import some icons for checking

describe('Tool Utils', () => {
  describe('getLabelForTool', () => {
    it('should return correct labels for known tool names', () => {
      expect(getLabelForTool('execute-command')).toBe('Execute Command');
      expect(getLabelForTool('web-search')).toBe('Web Search');
      expect(getLabelForTool('create-file')).toBe('Create File');
    });

    it('should return formatted label for generic tool names', () => {
      expect(getLabelForTool('my-custom-tool')).toBe('My Custom Tool');
    });

    it('should handle browser specific tools not explicitly mapped', () => {
      expect(getLabelForTool('browser-do-something')).toBe('Browser Do Something');
    });

    it('should return default for unmapped names that are not browser specific', () => {
      // Based on current getToolTitle, it will format it
      expect(getLabelForTool('unmapped_tool_name')).toBe('Unmapped Tool Name');
    });
  });

  describe('getIconForTool', () => {
    it('should return correct icons for known tool names', () => {
      expect(getIconForTool('execute-command')).toBe(Terminal);
      expect(getIconForTool('web-search')).toBe(Search);
      expect(getIconForTool('read-file')).toBe(FileText);
      expect(getIconForTool('browser-click')).toBe(MousePointerClick);
    });

    it('should return a default icon for unknown tool names', () => {
      expect(getIconForTool('this-tool-does-not-exist')).toBe(Wrench);
    });

    it('should return an icon for inferred tool names', () => {
      expect(getIconForTool('my-file-operation')).toBe(FileText); // Contains 'file'
      expect(getIconForTool('do-a-web-crawl')).toBe(Globe); // Contains 'web'
      expect(getIconForTool('super_custom_tool_for_puzzle')).toBe(Puzzle); // Default for generic 'generic-tool'
    });

    it('should return a React functional component', () => {
      const IconComponent = getIconForTool('execute-command');
      expect(typeof IconComponent).toBe('function');
      // A more robust check would be to render it and see, but that's for component tests.
      // For util tests, checking if it's a function (React components are functions) is often enough.
      // Also, checking the name of the function if it's not minified.
      // For Lucide icons, they are functions.
    });

     it('should handle underscores and casing', () => {
      expect(getIconForTool('EXECUTE_COMMAND')).toBe(Terminal);
      expect(getIconForTool('Web_Search')).toBe(Search);
    });

    it('should return default Puzzle for "generic-tool" or "default"', () => {
      expect(getIconForTool('generic-tool')).toBe(Puzzle);
      expect(getIconForTool('default')).toBe(Puzzle);
    });
  });
});
