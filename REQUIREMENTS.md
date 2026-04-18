This makes me think about an architectural issue with the context system. Informationally it's okay, it conveys what it conveys, but … I wonder if instead of

```
item

item

item
```

It'd be better if it were

```
[datetime (age)] item

[datetime (age)] item

[datetime (age)] item
```

That might help you recognize it more clearly that the information 