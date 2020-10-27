# Dayside Codeintel

[![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE) ![PyPI](https://img.shields.io/pypi/v/1)
![Awesome](https://camo.githubusercontent.com/fef0a78bf2b1b477ba227914e3eff273d9b9713d/68747470733a2f2f696d672e736869656c64732e696f2f62616467652f617765736f6d652533462d796573212d627269676874677265656e2e737667)
![Stable](https://img.shields.io/badge/status-stable-brightgreen)

Codeintel support for dayside

Python based [Dayside](https://github.com/boomyjee/dayside) plugin for autocomplete.  

## Features
- python, javascript and php support
- imports autocomplete - shows autocomplete with the available modules/symbols in real-time   
![completion](images/completion.gif)

- jump to symbol definition - jump to the file and line of the definition of a symbol
![go to definition](images/definition.gif)

## Install
Connect the plugin to dayside as usual. See example
```html
<!-- connect dayside script -->
<script src="client/dayside.js"></script>
<link href="client/dayside.css" rel="stylesheet" type="text/css">

<!-- connect codeintel script -->
<script src="<plugin_folder>/client/codeintel.js"></script>
<link href="<plugin_folder>/client/codeintel.css" rel="stylesheet" type="text/css">

<script>
    // run dayside
    dayside({
        //...dayside options
    });
    // run codeintel plugin
    dayside.plugins.codeintel({
        port: 4200 // port where backend server should start, default 8000
    });
</script>
```

### License

Plugin is [MIT licensed](./LICENSE).
